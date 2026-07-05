"""Reddit巡回

基本は認証不要のnew.jsonエンドポイント（レート制限厳守・User-Agent明記）。
RedditはデータセンターIP（GitHub Actionsランナー含む）を403でブロックすることがあるため、
以下の順でフォールバックする:
    1. OAuth (oauth.reddit.com)  ※ REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET 設定時のみ。最も安定
    2. https://www.reddit.com/r/{sub}/new.json
    3. https://old.reddit.com/r/{sub}/new.json
    4. https://www.reddit.com/r/{sub}/new/.rss  ※ upvote数が取れないため拡散速度補正は無効
"""
import os
import time
from typing import List, Optional

import feedparser
import requests

from .models import Item


def _get_oauth_token(ua: str, timeout: int) -> Optional[str]:
    cid = os.environ.get("REDDIT_CLIENT_ID", "")
    secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    if not cid or not secret:
        return None
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=(cid, secret),
        headers={"User-Agent": ua},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_posts_json(url: str, headers: dict, timeout: int) -> list:
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("children", [])


def _items_from_json(children: list, sub_name: str, trust: str) -> List[Item]:
    items = []
    for child in children:
        d = child.get("data", {})
        title = (d.get("title") or "").strip()
        permalink = d.get("permalink") or ""
        if not title or not permalink:
            continue
        items.append(Item(
            title=title,
            url=f"https://www.reddit.com{permalink}",
            source=f"r/{sub_name}",
            trust=trust,
            unverified=(trust == "leak"),
            ups=d.get("ups"),
            created_utc=d.get("created_utc"),
        ))
    return items


def _items_from_rss(url: str, ua: str, timeout: int, sub_name: str, trust: str) -> List[Item]:
    resp = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
    if resp.status_code == 429:  # レート制限: Retry-Afterを尊重して1回だけ再試行
        wait = min(float(resp.headers.get("Retry-After", 15)), 60)
        time.sleep(wait)
        resp = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        items.append(Item(
            title=title, url=link, source=f"r/{sub_name}", trust=trust,
            unverified=(trust == "leak"),
        ))
    return items


def fetch_subreddits(sources_cfg: dict, log) -> List[Item]:
    reddit_cfg = sources_cfg.get("reddit") or {}
    http_cfg = sources_cfg.get("http", {})
    ua = http_cfg.get("user_agent", "pazuu-trend-watcher/1.0")
    timeout = http_cfg.get("timeout_sec", 15)
    interval = reddit_cfg.get("request_interval_sec", 3)
    limit = reddit_cfg.get("limit", 25)

    token = None
    try:
        token = _get_oauth_token(ua, timeout)
        if token:
            log("Reddit: OAuthトークン取得成功")
    except Exception as e:
        log(f"Reddit: OAuthトークン取得失敗 ({e})。認証なしルートで続行")

    items: List[Item] = []
    subs = reddit_cfg.get("subreddits") or []
    dead_routes = set()  # 403等でブロック済みのルートは同一実行内で再試行しない（レート枠温存）
    for i, sub in enumerate(subs):
        name, trust = sub["name"], sub.get("trust", "community")
        got: Optional[List[Item]] = None

        # 1. OAuth → 2. www → 3. old.reddit（JSON: upvote速度が取れる）
        json_routes = []
        if token:
            json_routes.append((
                "oauth",
                f"https://oauth.reddit.com/r/{name}/new?limit={limit}&raw_json=1",
                {"User-Agent": ua, "Authorization": f"Bearer {token}"},
            ))
        json_routes += [
            ("www",
             f"https://www.reddit.com/r/{name}/new.json?limit={limit}&raw_json=1",
             {"User-Agent": ua}),
            ("old",
             f"https://old.reddit.com/r/{name}/new.json?limit={limit}&raw_json=1",
             {"User-Agent": ua}),
        ]
        for route, url, headers in json_routes:
            if route in dead_routes:
                continue
            try:
                children = _fetch_posts_json(url, headers, timeout)
                got = _items_from_json(children, name, trust)
                break
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (401, 403):  # IPブロック: 他サブレでも失敗するので以後スキップ
                    dead_routes.add(route)
            except Exception:
                continue

        # 4. RSSフォールバック（upvote数なし → 拡散速度補正は無効）
        if got is None:
            try:
                got = _items_from_rss(f"https://www.reddit.com/r/{name}/new/.rss",
                                      ua, timeout, name, trust)
                log(f"r/{name}: {len(got)}件（RSSフォールバック・拡散補正なし）")
            except Exception as e:
                log(f"r/{name}: 全ルート取得失敗 ({e})")
        else:
            log(f"r/{name}: {len(got)}件")

        if got:
            items.extend(got)
        if i < len(subs) - 1:
            time.sleep(interval)

    return items

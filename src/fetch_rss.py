"""RSSフィード巡回（ゲームメディア + Google News検索RSS）"""
import time
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import quote

import feedparser
import requests

from .models import Item

GOOGLE_NEWS_LOCALE = {
    "ja": "hl=ja&gl=JP&ceid=JP:ja",
    "en": "hl=en-US&gl=US&ceid=US:en",
}


def _entry_published(entry) -> Optional[datetime]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return None


def _fetch_feed(url: str, http_cfg: dict):
    resp = requests.get(
        url,
        headers={"User-Agent": http_cfg.get("user_agent", "pazuu-trend-watcher/1.0")},
        timeout=http_cfg.get("timeout_sec", 15),
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _feed_items(feed, source: str, trust: str, default_tier=None) -> List[Item]:
    items = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        items.append(Item(
            title=title, url=link, source=source, trust=trust,
            unverified=(trust == "leak"),
            published=_entry_published(entry),
            default_tier=default_tier,
        ))
    return items


def fetch_rss_sources(sources_cfg: dict, log) -> List[Item]:
    http_cfg = sources_cfg.get("http", {})
    items: List[Item] = []

    for src in sources_cfg.get("rss") or []:
        try:
            feed = _fetch_feed(src["url"], http_cfg)
            got = _feed_items(feed, src["name"], src.get("trust", "media"))
            items.extend(got)
            log(f"RSS {src['name']}: {len(got)}件")
        except Exception as e:  # 1ソースの失敗で全体を止めない
            log(f"RSS {src['name']}: 取得失敗 ({e})")

    for gn in sources_cfg.get("google_news") or []:
        query, lang = gn["query"], gn.get("lang", "ja")
        locale = GOOGLE_NEWS_LOCALE.get(lang, GOOGLE_NEWS_LOCALE["ja"])
        url = f"https://news.google.com/rss/search?q={quote(query)}&{locale}"
        name = f"Google News ({query}/{lang})"
        try:
            feed = _fetch_feed(url, http_cfg)
            # クエリ自体が絞り込みなので、辞書未ヒットでもB級として扱う
            got = _feed_items(feed, name, "media", default_tier="B")
            items.extend(got)
            log(f"{name}: {len(got)}件")
        except Exception as e:
            log(f"{name}: 取得失敗 ({e})")

    return items

"""Discord Webhook通知

- #速報-s級: 即時・メンション付き
- #ダイジェスト: A級1時間毎まとめ / B級日次まとめ
"""
import time

import requests

from .models import Detection, TRUST_LABELS, JST

DISCORD_CONTENT_LIMIT = 2000

NEXT_ACTION = "次のアクション: 一次ソース確認 → 事実確認後にClaude Proで台本骨子生成"


def format_s_alert(d: Detection, prompt: str) -> str:
    flag = "⚠️【未確認】" if d.item.unverified else ""
    trust_label = TRUST_LABELS.get(d.item.trust, d.item.trust)
    ts = d.detected_at.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    return (
        f"🚨 S級検知: {flag}{d.item.title}\n"
        f"ソース: {d.item.source}（信頼度: {trust_label}）\n"
        f"URL: {d.item.url}\n"
        f"検知時刻: {ts}\n"
        f"（スコア: {d.score} / キーワード: {d.matched_keyword}）\n"
        f"---\n"
        f"📌 視聴者価値の観点（このニュースでチューマは何を知りたい？）\n"
        f"・{prompt}\n"
        f"---\n"
        f"{NEXT_ACTION}"
    )


def format_digest(tier: str, rows: list, period_label: str) -> str:
    """rows: (id, title, url, source, trust, score, unverified, detected_at)"""
    header = f"📋 {tier}級ダイジェスト（{period_label}・{len(rows)}件）\n"
    lines = []
    for n, (_id, title, url, source, trust, score, unverified, _at) in enumerate(rows, 1):
        flag = "⚠️【未確認】" if unverified else ""
        trust_label = TRUST_LABELS.get(trust, trust)
        lines.append(f"{n}. {flag}[{trust_label}/{source}] {title}\n   {url} (score: {score})")
    return header + "\n".join(lines)


def _post(webhook_url: str, content: str, timeout: int = 15):
    resp = requests.post(webhook_url, json={"content": content}, timeout=timeout)
    if resp.status_code == 429:
        retry = float(resp.headers.get("Retry-After", 2))
        time.sleep(min(retry, 10))
        resp = requests.post(webhook_url, json={"content": content}, timeout=timeout)
    resp.raise_for_status()


def _chunks(content: str) -> list:
    """Discordの2000文字制限に合わせ行単位で分割"""
    if len(content) <= DISCORD_CONTENT_LIMIT:
        return [content]
    chunks, buf = [], ""
    for line in content.split("\n"):
        line = line[:DISCORD_CONTENT_LIMIT]
        if len(buf) + len(line) + 1 > DISCORD_CONTENT_LIMIT:
            chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else f"{buf}\n{line}"
    if buf:
        chunks.append(buf)
    return chunks


def send(webhook_url: str, content: str, dry_run: bool, log) -> bool:
    """通知送信。成功(またはdry-run)でTrue"""
    if dry_run:
        log("--- [DRY RUN] 通知内容 ---\n" + content + "\n--------------------------")
        return True
    if not webhook_url:
        log("Webhook URL未設定のため通知スキップ")
        return False
    try:
        for chunk in _chunks(content):
            _post(webhook_url, chunk)
        return True
    except Exception as e:
        log(f"Discord通知失敗: {e}")
        return False

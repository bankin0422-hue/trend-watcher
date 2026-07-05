"""トレンド速報ウォッチャー メインエントリ（1サイクル実行してexit）

GitHub Actionsから15分毎に呼ばれる想定。ローカル確認は:
    python -m src.main --dry-run

環境変数:
    DISCORD_WEBHOOK_S       #速報-s級 のWebhook URL（必須）
    DISCORD_WEBHOOK_DIGEST  #ダイジェスト のWebhook URL（省略時はS級と同じ先）
    DISCORD_MENTION         S級通知の先頭に付けるメンション（例: @everyone, <@&ロールID>）
    DRY_RUN                 "1"で送信せず標準出力へ
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime

from .config import load_keywords, load_sources, DATA_DIR
from .fetch_reddit import fetch_subreddits
from .fetch_rss import fetch_rss_sources
from .models import JST
from .notifier import format_digest, format_s_alert, send
from .scorer import score_item, viewer_value_prompt
from .storage import Storage

DIGEST_INTERVALS = {"A": 3600, "B": 86400}  # A級: 1時間毎 / B級: 日次
DIGEST_LABELS = {"A": "1時間毎", "B": "日次"}


def log(msg: str):
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def _s_cooldown_active(storage, keyword: str, cooldown_sec: float) -> bool:
    """同一キーワードのS級即時通知クールダウン。
    大ニュースは各メディアが一斉に報じるため、初報のみ即時・続報はA級ダイジェストへ。
    """
    key = "s_cooldown_" + hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:16]
    last = storage.get_meta(key)
    now = time.time()
    if last is not None and now - float(last) < cooldown_sec:
        return True
    storage.set_meta(key, str(now))
    return False


def process_items(items, keywords_cfg, reddit_cfg, storage, webhook_s, mention, dry_run):
    now_iso = datetime.now(JST).isoformat()
    cooldown_sec = keywords_cfg["scoring"].get("s_cooldown_hours", 6) * 3600
    new_count = s_count = queued = 0

    for item in items:
        if storage.is_seen(item.url):
            continue
        storage.mark_seen(item.url, now_iso)
        new_count += 1

        detection = score_item(item, keywords_cfg, reddit_cfg)
        if detection is None:
            continue

        if detection.tier == "S" and _s_cooldown_active(storage, detection.matched_keyword,
                                                        cooldown_sec):
            detection.tier = "A"  # 続報扱いでダイジェストへ降格
            log(f"S級クールダウン中のためA級扱い: {item.title[:60]}")

        if detection.tier == "S":
            prompt = viewer_value_prompt(item.title, keywords_cfg)
            content = format_s_alert(detection, prompt)
            if mention:
                content = f"{mention}\n{content}"
            notified = send(webhook_s, content, dry_run, log)
            storage.add_history(detection, notified)
            s_count += 1
        else:
            storage.enqueue_digest(detection)
            storage.add_history(detection, False)
            queued += 1

    log(f"新着{new_count}件 / S級即時通知{s_count}件 / ダイジェスト積み{queued}件")


def flush_digests(storage, webhook_digest, dry_run):
    now = time.time()
    for tier, interval in DIGEST_INTERVALS.items():
        if now - storage.last_flush(tier) < interval:
            continue
        rows = storage.pending_digest(tier)
        if not rows:
            storage.set_last_flush(tier, now)
            continue
        content = format_digest(tier, rows, DIGEST_LABELS[tier])
        if send(webhook_digest, content, dry_run, log):
            storage.mark_flushed([r[0] for r in rows])
            storage.set_last_flush(tier, now)
            log(f"{tier}級ダイジェスト送信: {len(rows)}件")


def main():
    parser = argparse.ArgumentParser(description="トレンド速報ウォッチャー（1サイクル実行）")
    parser.add_argument("--dry-run", action="store_true", help="Discordへ送信せず標準出力へ")
    args = parser.parse_args()

    dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
    webhook_s = os.environ.get("DISCORD_WEBHOOK_S", "")
    webhook_digest = os.environ.get("DISCORD_WEBHOOK_DIGEST", "") or webhook_s
    mention = os.environ.get("DISCORD_MENTION", "")

    if not dry_run and not webhook_s:
        log("エラー: DISCORD_WEBHOOK_S が未設定（--dry-run なら送信なしで動作確認可）")
        sys.exit(1)

    keywords_cfg = load_keywords()
    sources_cfg = load_sources()
    reddit_cfg = sources_cfg.get("reddit") or {}
    storage = Storage(DATA_DIR / "watcher.db")

    try:
        items = fetch_rss_sources(sources_cfg, log)
        items += fetch_subreddits(sources_cfg, log)
        log(f"巡回完了: 合計{len(items)}件取得")

        if storage.get_meta("initialized") is None:
            # 初回はバックログ（過去記事）を既読登録のみして通知しない
            now_iso = datetime.now(JST).isoformat()
            for item in items:
                storage.mark_seen(item.url, now_iso)
            storage.set_meta("initialized", "1")
            log(f"初回シード完了: {len(items)}件を既読登録（通知なし）。次回実行から検知開始")
        else:
            process_items(items, keywords_cfg, reddit_cfg, storage,
                          webhook_s, mention, dry_run)
            flush_digests(storage, webhook_digest, dry_run)
        storage.commit()
    finally:
        storage.close()


if __name__ == "__main__":
    main()

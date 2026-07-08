"""トレンド速報ウォッチャー メインエントリ（1サイクル実行してexit）

GitHub Actionsから15分毎に巡回・検知するが、通知メールはS/A/B級すべてまとめて1日1回のみ送信する。
ローカル確認は:
    python -m src.main --dry-run

環境変数:
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD  # SMTP設定（必須）
    MAIL_FROM               送信元アドレス（省略時はSMTP_USER）
    MAIL_TO                 送信先アドレス（必須）
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
from .notifier import daily_digest_subject, format_daily_digest, send_mail
from .scorer import score_item
from .storage import Storage

DAILY_INTERVAL_SEC = 86400
DAILY_LABEL = "日次"


def log(msg: str):
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def _s_cooldown_active(storage, keyword: str, cooldown_sec: float) -> bool:
    """同一キーワードのS級クールダウン。
    大ニュースは各メディアが一斉に報じるため、日次ダイジェストのS級欄が同じ話題で
    埋まらないよう、初報のみS級・続報はA級として積む。
    """
    key = "s_cooldown_" + hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:16]
    last = storage.get_meta(key)
    now = time.time()
    if last is not None and now - float(last) < cooldown_sec:
        return True
    storage.set_meta(key, str(now))
    return False


def process_items(items, keywords_cfg, reddit_cfg, storage):
    now_iso = datetime.now(JST).isoformat()
    cooldown_sec = keywords_cfg["scoring"].get("s_cooldown_hours", 6) * 3600
    new_count = queued = 0

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

        storage.enqueue_digest(detection)
        storage.add_history(detection, False)
        queued += 1

    log(f"新着{new_count}件 / ダイジェスト積み{queued}件")


def flush_digest(storage, smtp_cfg, keywords_cfg, dry_run):
    now = time.time()
    if now - storage.last_flush("daily") < DAILY_INTERVAL_SEC:
        return
    rows_by_tier = {tier: storage.pending_digest(tier) for tier in ("S", "A", "B")}
    all_ids = [row[0] for rows in rows_by_tier.values() for row in rows]
    if not all_ids:
        storage.set_last_flush("daily", now)
        return
    content = format_daily_digest(rows_by_tier, keywords_cfg, DAILY_LABEL)
    subject = daily_digest_subject(rows_by_tier, DAILY_LABEL)
    if send_mail(smtp_cfg, subject, content, dry_run, log):
        storage.mark_flushed(all_ids)
        storage.set_last_flush("daily", now)
        log(f"日次ダイジェスト送信: 合計{len(all_ids)}件")


def main():
    parser = argparse.ArgumentParser(description="トレンド速報ウォッチャー（1サイクル実行）")
    parser.add_argument("--dry-run", action="store_true", help="メール送信せず標準出力へ")
    args = parser.parse_args()

    dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
    smtp_cfg = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": os.environ.get("SMTP_PORT", "587"),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "mail_from": os.environ.get("MAIL_FROM", ""),
        "mail_to": os.environ.get("MAIL_TO", ""),
    }

    if not dry_run and not (smtp_cfg["host"] and smtp_cfg["user"] and smtp_cfg["password"]
                             and smtp_cfg["mail_to"]):
        # Secrets登録前でもActionsを失敗させない（巡回・既読管理は動かし、送信のみ省略）
        log("警告: SMTP設定が未完了のためdry-runで実行（通知は送信されない）")
        dry_run = True

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
            process_items(items, keywords_cfg, reddit_cfg, storage)
            flush_digest(storage, smtp_cfg, keywords_cfg, dry_run)
        storage.commit()
    finally:
        storage.close()


if __name__ == "__main__":
    main()

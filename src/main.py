"""トレンド速報ウォッチャー メインエントリ（1サイクル実行してexit）

GitHub Actionsから15分毎に巡回・検知するが、通知メールはS/A/B級すべてまとめて
毎朝SEND_HOUR_JST時(JST)を過ぎた最初の巡回で1日1回のみ送信する。
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
from .notifier import (MAIL_TIERS, daily_digest_subject, dedup_rows,
                       format_daily_digest, send_mail)
from .scorer import score_item
from .storage import Storage

SEND_HOUR_JST = 7  # 毎日この時刻(JST)を過ぎた最初の巡回でダイジェストを送信
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

        storage.add_history(detection, False)  # 履歴は全級保存（レビュー用）
        if detection.tier in MAIL_TIERS:       # メール対象(S/A)のみキューへ。B級は履歴のみ
            storage.enqueue_digest(detection)
            queued += 1

    log(f"新着{new_count}件 / メール対象積み{queued}件")


def _daily_send_due(storage) -> bool:
    """毎日SEND_HOUR_JST時を過ぎた最初の巡回でTrue（1日1回・時刻固定でドリフトしない）"""
    now = datetime.now(JST)
    if now.hour < SEND_HOUR_JST:
        return False
    last = storage.get_meta("last_flush_daily")
    if last is None:
        return True
    last_sent = datetime.fromtimestamp(float(last), JST)
    return last_sent.date() < now.date()  # 今日まだ送っていなければ送信


def flush_digest(storage, smtp_cfg, keywords_cfg, dry_run):
    if not _daily_send_due(storage):
        return
    now = time.time()
    rows_by_tier = {tier: storage.pending_digest(tier) for tier in MAIL_TIERS}
    all_ids = [row[0] for rows in rows_by_tier.values() for row in rows]
    if not all_ids:
        storage.set_last_flush("daily", now)
        return
    # 近似重複を集約して表示（マーク対象は元の全idなので次回に持ち越さない）
    deduped_by_tier = {tier: dedup_rows(rows) for tier, rows in rows_by_tier.items()}
    content = format_daily_digest(deduped_by_tier, keywords_cfg, DAILY_LABEL)
    subject = daily_digest_subject(deduped_by_tier, DAILY_LABEL)
    if send_mail(smtp_cfg, subject, content, dry_run, log):
        storage.mark_flushed(all_ids)
        storage.set_last_flush("daily", now)
        distinct = sum(len(v) for v in deduped_by_tier.values())
        log(f"日次ダイジェスト送信: {distinct}件（重複集約前 {len(all_ids)}件）")


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

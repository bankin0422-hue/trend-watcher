"""メール通知（SMTP）

S/A/B級すべてまとめて1日1回のダイジェストメールで送信する。
"""
import smtplib
from email.mime.text import MIMEText

from .models import TRUST_LABELS
from .scorer import viewer_value_prompt

NEXT_ACTION = "次のアクション: 一次ソース確認 → 事実確認後にClaude Proで台本骨子生成"

TIER_HEADERS = {"S": "🚨 S級速報", "A": "📈 A級", "B": "📰 B級"}


def format_daily_digest(rows_by_tier: dict, keywords_cfg: dict, period_label: str) -> str:
    """rows_by_tier: {"S"|"A"|"B": [(id, title, url, source, trust, score, unverified, detected_at), ...]}"""
    total = sum(len(rows) for rows in rows_by_tier.values())
    parts = [f"📋 トレンドダイジェスト（{period_label}・{total}件）"]

    for tier in ("S", "A", "B"):
        rows = rows_by_tier.get(tier) or []
        if not rows:
            continue
        parts.append(f"\n=== {TIER_HEADERS[tier]}（{len(rows)}件） ===")
        if tier == "S":
            for _id, title, url, source, trust, score, unverified, _at in rows:
                flag = "⚠️【未確認】" if unverified else ""
                trust_label = TRUST_LABELS.get(trust, trust)
                prompt = viewer_value_prompt(title, keywords_cfg)
                parts.append(
                    f"{flag}{title}\n"
                    f"  ソース: {source}（信頼度: {trust_label}） / URL: {url} (score: {score})\n"
                    f"  📌 視聴者価値: {prompt}"
                )
            parts.append(NEXT_ACTION)
        else:
            lines = []
            for n, (_id, title, url, source, trust, score, unverified, _at) in enumerate(rows, 1):
                flag = "⚠️【未確認】" if unverified else ""
                trust_label = TRUST_LABELS.get(trust, trust)
                lines.append(f"{n}. {flag}[{trust_label}/{source}] {title}\n   {url} (score: {score})")
            parts.append("\n".join(lines))

    return "\n".join(parts)


def daily_digest_subject(rows_by_tier: dict, period_label: str) -> str:
    total = sum(len(rows) for rows in rows_by_tier.values())
    s_count = len(rows_by_tier.get("S") or [])
    if s_count:
        return f"[トレンド日次] {period_label}・S級{s_count}件含む計{total}件"
    return f"[トレンド日次] {period_label}・計{total}件"


def send_mail(smtp_cfg: dict, subject: str, body: str, dry_run: bool, log) -> bool:
    """メール送信。成功(またはdry-run)でTrue"""
    if dry_run:
        log(f"--- [DRY RUN] 件名: {subject} ---\n" + body + "\n--------------------------")
        return True
    if not (smtp_cfg.get("host") and smtp_cfg.get("user") and smtp_cfg.get("password")
            and smtp_cfg.get("mail_to")):
        log("SMTP設定未完了のため通知スキップ")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp_cfg.get("mail_from") or smtp_cfg["user"]
        msg["To"] = smtp_cfg["mail_to"]
        with smtplib.SMTP(smtp_cfg["host"], int(smtp_cfg.get("port") or 587), timeout=15) as server:
            server.starttls()
            server.login(smtp_cfg["user"], smtp_cfg["password"])
            server.sendmail(msg["From"], [smtp_cfg["mail_to"]], msg.as_string())
        return True
    except Exception as e:
        log(f"メール送信失敗: {e}")
        return False

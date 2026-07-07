"""メール通知（SMTP）

- S級: 即時メール
- A級: 1時間毎まとめメール / B級: 日次まとめメール
"""
import smtplib
from email.mime.text import MIMEText

from .models import Detection, TRUST_LABELS, JST

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


def s_alert_subject(d: Detection) -> str:
    flag = "【未確認】" if d.item.unverified else ""
    return f"[S級速報] {flag}{d.item.title[:80]}"


def format_digest(tier: str, rows: list, period_label: str) -> str:
    """rows: (id, title, url, source, trust, score, unverified, detected_at)"""
    header = f"📋 {tier}級ダイジェスト（{period_label}・{len(rows)}件）\n"
    lines = []
    for n, (_id, title, url, source, trust, score, unverified, _at) in enumerate(rows, 1):
        flag = "⚠️【未確認】" if unverified else ""
        trust_label = TRUST_LABELS.get(trust, trust)
        lines.append(f"{n}. {flag}[{trust_label}/{source}] {title}\n   {url} (score: {score})")
    return header + "\n".join(lines)


def digest_subject(tier: str, rows: list, period_label: str) -> str:
    return f"[{tier}級ダイジェスト] {period_label}・{len(rows)}件"


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

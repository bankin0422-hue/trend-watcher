"""メール通知（SMTP）

S級とA級をまとめて1日1回のダイジェストメールで送信する（B級はメールに載せない）。
同一ニュースの他媒体転載・サブレ跨ぎの再投稿は近似重複として1行に集約する。
"""
import re
import smtplib
from email.mime.text import MIMEText

from .models import TRUST_LABELS
from .scorer import viewer_value_prompt

NEXT_ACTION = "次のアクション: 一次ソース確認 → 事実確認後にClaude Proで台本骨子生成"

TIER_HEADERS = {"S": "🚨 S級速報", "A": "📈 A級"}
MAIL_TIERS = ("S", "A")  # メールに載せる級（B級は履歴のみ・メール非掲載）

# ' - 媒体名' / ' — 媒体名' のような末尾の媒体表記
_OUTLET_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]+$")
# ひらがな・カタカナ(぀-ヿ) + CJK統合漢字(一-鿿)
_TOKEN_STRIP = re.compile(r"[^0-9a-z぀-ヿ一-鿿]+")
_CJK = re.compile(r"[぀-ヿ一-鿿]")
DEDUP_THRESHOLD = 0.75


def _title_tokens(title: str) -> set:
    """タイトルを言語混在に強いトークン集合へ。ラテン語は単語、CJKは文字bigram。"""
    t = _OUTLET_SUFFIX.sub("", title).lower()
    t = _TOKEN_STRIP.sub(" ", t)
    tokens = set()
    for w in t.split():
        if _CJK.search(w):
            if len(w) == 1:
                tokens.add(w)
            for i in range(len(w) - 1):
                tokens.add(w[i:i + 2])
        elif len(w) > 1:
            tokens.add(w)
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedup_rows(rows: list, threshold: float = DEDUP_THRESHOLD) -> list:
    """近似重複をまとめる。rowsはscore降順前提。戻り値: [(代表row, 重複件数), ...]"""
    clusters = []  # [{"tokens": set, "row": row, "count": int}]
    for row in rows:
        toks = _title_tokens(row[1])
        for c in clusters:
            if _jaccard(toks, c["tokens"]) >= threshold:
                c["count"] += 1
                break
        else:
            clusters.append({"tokens": toks, "row": row, "count": 1})
    return [(c["row"], c["count"]) for c in clusters]


def _dup_note(count: int) -> str:
    return f"（+{count - 1}媒体）" if count > 1 else ""


def format_daily_digest(deduped_by_tier: dict, keywords_cfg: dict, period_label: str) -> str:
    """deduped_by_tier: {"S"|"A": [(row, count), ...]}  row=(id,title,url,source,trust,score,unverified,detected_at)"""
    total = sum(len(rows) for rows in deduped_by_tier.values())
    parts = [f"📋 トレンドダイジェスト（{period_label}・{total}件）"]

    for tier in MAIL_TIERS:
        rows = deduped_by_tier.get(tier) or []
        if not rows:
            continue
        parts.append(f"\n=== {TIER_HEADERS[tier]}（{len(rows)}件） ===")
        if tier == "S":
            for (_id, title, url, source, trust, score, unverified, _at), count in rows:
                flag = "⚠️【未確認】" if unverified else ""
                trust_label = TRUST_LABELS.get(trust, trust)
                prompt = viewer_value_prompt(title, keywords_cfg)
                parts.append(
                    f"{flag}{title}{_dup_note(count)}\n"
                    f"  ソース: {source}（信頼度: {trust_label}） / URL: {url} (score: {score})\n"
                    f"  📌 視聴者価値: {prompt}"
                )
            parts.append(NEXT_ACTION)
        else:
            lines = []
            for n, ((_id, title, url, source, trust, score, unverified, _at), count) in enumerate(rows, 1):
                flag = "⚠️【未確認】" if unverified else ""
                trust_label = TRUST_LABELS.get(trust, trust)
                lines.append(
                    f"{n}. {flag}[{trust_label}/{source}] {title}{_dup_note(count)}\n"
                    f"   {url} (score: {score})"
                )
            parts.append("\n".join(lines))

    return "\n".join(parts)


def daily_digest_subject(deduped_by_tier: dict, period_label: str) -> str:
    total = sum(len(rows) for rows in deduped_by_tier.values())
    s_count = len(deduped_by_tier.get("S") or [])
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

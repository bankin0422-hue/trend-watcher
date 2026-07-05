"""キーワードマッチ＋スコアリング

スコア = キーワード級の重み × ソース信頼度 × 拡散速度補正
閾値超えで昇格（例: B級キーワードでもRedditで急伸していればA級扱い）
"""
import re
import time
from functools import lru_cache
from typing import Optional

from .models import Item, Detection


@lru_cache(maxsize=1024)
def _term_regex(term: str) -> re.Pattern:
    """英数字で始まる/終わる語には語境界を要求する。
    例: "Cyberpunk 2" が "Cyberpunk 2077" に誤爆しない。日本語はそのまま部分一致。
    """
    prefix = r"(?<![A-Za-z0-9])" if term[0].isascii() and term[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if term[-1].isascii() and term[-1].isalnum() else ""
    return re.compile(prefix + re.escape(term) + suffix, re.IGNORECASE)


def _hit(title: str, entry: str) -> bool:
    """"A+B" 記法: すべての語を含む場合ヒット"""
    terms = [p.strip() for p in entry.split("+") if p.strip()]
    return bool(terms) and all(_term_regex(t).search(title) for t in terms)


def match_keyword(title: str, keywords: dict) -> Optional[tuple]:
    """S→A→Bの順で最初にヒットした (級, キーワード) を返す"""
    for tier in ("S", "A", "B"):
        for entry in keywords.get(tier) or []:
            if _hit(title, str(entry)):
                return tier, str(entry)
    return None


def spread_factor(item: Item, reddit_cfg: dict) -> float:
    """Reddit upvote速度による拡散補正（RSS等は1.0）"""
    if item.ups is None or item.created_utc is None:
        return 1.0
    age_h = (time.time() - item.created_utc) / 3600
    max_age = reddit_cfg.get("velocity_max_age_hours", 6)
    if age_h <= 0 or age_h > max_age:
        return 1.0
    velocity = item.ups / max(age_h, 0.25)  # 票/時（投稿直後の過大評価を抑制）
    ref = reddit_cfg.get("velocity_ref", 50)
    cap = reddit_cfg.get("spread_cap", 3.0)
    return 1.0 + min(velocity / ref, cap)


def score_item(item: Item, keywords_cfg: dict, reddit_cfg: dict) -> Optional[Detection]:
    """辞書に基づきスコアリング。無関係なら None"""
    scoring = keywords_cfg["scoring"]
    matched = match_keyword(item.title, keywords_cfg["keywords"])
    if matched is None:
        if item.default_tier is None:
            return None
        keyword_tier, keyword = item.default_tier, f"(ソース既定: {item.source})"
    else:
        keyword_tier, keyword = matched

    weight = scoring["tier_weights"][keyword_tier]
    trust_mult = scoring["trust_multipliers"].get(item.trust, 1.0)
    spread = spread_factor(item, reddit_cfg)
    score = weight * trust_mult * spread

    th = scoring["thresholds"]
    if score >= th["S"]:
        tier = "S"
    elif score >= th["A"]:
        tier = "A"
    else:
        tier = "B"

    return Detection(item=item, matched_keyword=keyword, keyword_tier=keyword_tier,
                     score=round(score, 1), tier=tier, spread=round(spread, 2))


def viewer_value_prompt(title: str, keywords_cfg: dict) -> str:
    """視聴者価値の観点（このニュースでチューマは何を知りたい？）"""
    vv = keywords_cfg.get("viewer_value", {})
    t = title.lower()
    for rule in vv.get("rules") or []:
        if any(str(c).lower() in t for c in rule.get("contains", [])):
            return rule["prompt"]
    return vv.get("default", "このニュースでチューマの視聴体験は何が変わるか")

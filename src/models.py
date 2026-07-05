from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

JST = timezone(timedelta(hours=9))

TRUST_LABELS = {
    "official": "公式",
    "media": "報道",
    "community": "コミュニティ",
    "leak": "リーク未確認",
}


@dataclass
class Item:
    """巡回で拾った1件のニュース/投稿"""
    title: str
    url: str
    source: str                      # 媒体名（例: "AUTOMATON", "r/cyberpunkgame"）
    trust: str                       # official / media / community / leak
    unverified: bool = False         # リーク由来フラグ
    published: Optional[datetime] = None
    ups: Optional[int] = None        # Redditのみ
    created_utc: Optional[float] = None  # Redditのみ
    default_tier: Optional[str] = None   # 辞書未ヒット時のフォールバック級（Google News用）


@dataclass
class Detection:
    """スコアリング済みの検知結果"""
    item: Item
    matched_keyword: str
    keyword_tier: str    # 辞書上の級
    score: float
    tier: str            # スコアによる最終分類（S/A/B）
    spread: float = 1.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(JST))

"""既読管理・検知履歴・ダイジェストキュー（SQLite / リポジトリ内で完結）"""
import hashlib
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    url TEXT,
    first_seen TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT,
    source TEXT,
    title TEXT,
    url TEXT,
    matched_keyword TEXT,
    keyword_tier TEXT,
    tier TEXT,
    score REAL,
    unverified INTEGER,
    notified INTEGER
);
CREATE TABLE IF NOT EXISTS digest_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT,
    title TEXT,
    url TEXT,
    source TEXT,
    trust TEXT,
    score REAL,
    unverified INTEGER,
    detected_at TEXT,
    flushed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def normalize_url(url: str) -> str:
    """トラッキングパラメータ等を除去して同一URLの重複通知を防ぐ"""
    try:
        p = urlsplit(url.strip())
        query = [(k, v) for k, v in parse_qsl(p.query)
                 if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ""))
    except ValueError:
        return url.strip()


def url_key(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---- 既読管理 ----
    def is_seen(self, url: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen WHERE key = ?", (url_key(url),))
        return cur.fetchone() is not None

    def mark_seen(self, url: str, now_iso: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (key, url, first_seen) VALUES (?, ?, ?)",
            (url_key(url), normalize_url(url), now_iso),
        )

    # ---- 検知履歴（全件保存: 後日の検知精度レビュー用） ----
    def add_history(self, d, notified: bool):
        self.conn.execute(
            "INSERT INTO history (detected_at, source, title, url, matched_keyword,"
            " keyword_tier, tier, score, unverified, notified)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.detected_at.isoformat(), d.item.source, d.item.title, d.item.url,
             d.matched_keyword, d.keyword_tier, d.tier, d.score,
             int(d.item.unverified), int(notified)),
        )

    # ---- ダイジェストキュー ----
    def enqueue_digest(self, d):
        self.conn.execute(
            "INSERT INTO digest_queue (tier, title, url, source, trust, score,"
            " unverified, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d.tier, d.item.title, d.item.url, d.item.source, d.item.trust,
             d.score, int(d.item.unverified), d.detected_at.isoformat()),
        )

    def pending_digest(self, tier: str) -> list:
        cur = self.conn.execute(
            "SELECT id, title, url, source, trust, score, unverified, detected_at"
            " FROM digest_queue WHERE tier = ? AND flushed = 0 ORDER BY score DESC",
            (tier,),
        )
        return cur.fetchall()

    def mark_flushed(self, ids: list):
        self.conn.executemany(
            "UPDATE digest_queue SET flushed = 1 WHERE id = ?", [(i,) for i in ids]
        )

    # ---- メタ情報（ダイジェスト送信時刻など） ----
    def get_meta(self, key: str, default=None):
        cur = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def last_flush(self, tier: str) -> float:
        val = self.get_meta(f"last_flush_{tier}")
        if val is None:
            # 初回はいまを起点にする（過去分の空ダイジェスト送信を防ぐ）
            now = time.time()
            self.set_meta(f"last_flush_{tier}", str(now))
            return now
        return float(val)

    def set_last_flush(self, tier: str, ts: float):
        self.set_meta(f"last_flush_{tier}", str(ts))

    def commit(self):
        self.conn.commit()

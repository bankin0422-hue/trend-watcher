"""フェーズ1受け入れ基準のオフライン確認（python test_smoke.py で実行）"""
import time

from src.config import load_keywords
from src.models import Item
from src.scorer import match_keyword, score_item
from src.storage import Storage, normalize_url
from src.notifier import format_s_alert, format_digest
from src.scorer import viewer_value_prompt

kw = load_keywords()
K = kw["keywords"]
failed = []


def check(name, cond):
    print(f"[{'OK' if cond else 'NG'}] {name}")
    if not cond:
        failed.append(name)


# --- キーワードマッチ（語境界: "Cyberpunk 2" が 2077 に誤爆しない） ---
tests = [
    ("Cyberpunk 2077 sold 40 million copies", "B"),
    ("The Best Deals Today: Cyberpunk 2077, Criterion Sale", "B"),
    ("CD Projekt confirms Cyberpunk 2 development", "S"),
    ("Project Orion enters full production", "S"),
    ("Edgerunners Season 2 release date announced", "S"),
    ("エッジランナーズ シーズン2、2026年秋に配信決定", "S"),
    ("サイバーパンク2077 大型アップデート配信", "A"),
    ("Random unrelated news about Zelda", None),
]
for title, expect in tests:
    m = match_keyword(title, K)
    tier = m[0] if m else None
    check(f"match: {title[:45]} -> {tier} (expect {expect})", tier == expect)

# --- 拡散昇格: B級キーワード + Reddit急伸 → A級 ---
hot = Item(title="Cyberpunk 2077 crazy discovery", url="https://reddit.com/x",
           source="r/cyberpunkgame", trust="community",
           ups=200, created_utc=time.time() - 3600)
d = score_item(hot, kw, {"velocity_ref": 50, "spread_cap": 3.0, "velocity_max_age_hours": 6})
check(f"B級+急伸(200up/h) -> {d.tier} score={d.score} (expect A)", d.tier == "A")

# --- リークサブレのS級キーワード → 未確認フラグ付きでS通知 ---
leak = Item(title="LEAK: Project Orion map size revealed", url="https://reddit.com/y",
            source="r/GamingLeaksAndRumours", trust="leak", unverified=True)
d2 = score_item(leak, kw, {})
check(f"S級×リークサブレ -> {d2.tier} unverified (expect S/未確認)",
      d2.tier == "S" and d2.item.unverified)
alert = format_s_alert(d2, viewer_value_prompt(d2.item.title, kw))
check("S級通知に未確認フラグと視聴者価値欄を含む",
      "未確認" in alert and "視聴者価値の観点" in alert and "次のアクション" in alert)

# --- 既読管理: 同一URL（トラッキングパラメータ差含む）は再通知しない ---
db = Storage(__import__("pathlib").Path("data") / "test_smoke.db")
url = "https://example.com/news/1?utm_source=twitter"
check("初見URLは未既読", not db.is_seen(url))
db.mark_seen(url, "2026-07-05T00:00:00")
check("同一URLは既読", db.is_seen("https://example.com/news/1"))
check("normalize_urlでutm除去",
      normalize_url(url) == "https://example.com/news/1")

# --- ダイジェスト整形 ---
db.enqueue_digest(d)
rows = db.pending_digest("A")
check("A級キューに1件", len(rows) == 1)
digest = format_digest("A", rows, "1時間毎")
check("ダイジェスト本文にタイトルとURL", "crazy discovery" in digest and "reddit.com" in digest)
db.mark_flushed([r[0] for r in rows])
check("flush後はキュー空", len(db.pending_digest("A")) == 0)
db.close()
__import__("os").remove("data/test_smoke.db")

print()
print("ALL OK" if not failed else f"FAILED: {len(failed)}")
raise SystemExit(1 if failed else 0)

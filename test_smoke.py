"""フェーズ1受け入れ基準のオフライン確認（python test_smoke.py で実行）"""
import time

from src.config import load_keywords
from src.models import Item
from src.scorer import match_keyword, score_item
from src.storage import Storage, normalize_url
from src.notifier import MAIL_TIERS, dedup_rows, format_daily_digest

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

# --- リークサブレのS級キーワード → 未確認フラグ付きでダイジェストのS級欄に入る ---
leak = Item(title="LEAK: Project Orion map size revealed", url="https://reddit.com/y",
            source="r/GamingLeaksAndRumours", trust="leak", unverified=True)
d2 = score_item(leak, kw, {})
check(f"S級×リークサブレ -> {d2.tier} unverified (expect S/未確認)",
      d2.tier == "S" and d2.item.unverified)

# --- 既読管理: 同一URL（トラッキングパラメータ差含む）は再通知しない ---
db = Storage(__import__("pathlib").Path("data") / "test_smoke.db")
url = "https://example.com/news/1?utm_source=twitter"
check("初見URLは未既読", not db.is_seen(url))
db.mark_seen(url, "2026-07-05T00:00:00")
check("同一URLは既読", db.is_seen("https://example.com/news/1"))
check("normalize_urlでutm除去",
      normalize_url(url) == "https://example.com/news/1")

# --- B級はメールキューに積まない（履歴のみ） ---
bee = Item(title="Cyberpunk 2077 GPU benchmark on old card", url="https://example.com/b",
           source="Google News (Cyberpunk 2077/en)", trust="media", default_tier="B")
d_b = score_item(bee, kw, {})
check(f"B級判定 -> {d_b.tier} (expect B)", d_b.tier == "B")

# --- 近似重複の集約（dedup_rows単体） ---
# 同一ニュースの他媒体転載（末尾の媒体名だけ違う）とサブレ跨ぎ再投稿を1件にまとめる
row_a1 = (1, "Witcher 3 Songs of the Past Reveal Date Confirmed - IGN",
          "https://x/1", "IGN", "media", 48.0, 0, "2026-07-21T00:00:00")
row_a2 = (2, "Witcher 3 Songs of the Past Reveal Date Confirmed - IGN Africa",
          "https://x/2", "IGN Africa", "media", 48.0, 0, "2026-07-21T00:00:00")
row_a3 = (3, "Edgerunners 2 gets a release window with new poster",
          "https://x/3", "r/Edgerunners", "community", 40.0, 0, "2026-07-21T00:00:00")
deduped_a = dedup_rows([row_a1, row_a2, row_a3])
check("他媒体転載を集約して2クラスタに", len(deduped_a) == 2)
check("先頭クラスタの重複件数=2", deduped_a[0][1] == 2)
check("別ニュースは集約されず単独", deduped_a[1][1] == 1)

# --- 日次ダイジェスト整形（S級・A級を1通にまとめる） ---
db.enqueue_digest(d2)   # S級1件
db.enqueue_digest(d)    # A級1件
rows_by_tier = {tier: db.pending_digest(tier) for tier in MAIL_TIERS}
check("S級キューに1件", len(rows_by_tier["S"]) == 1)
check("A級キューに1件", len(rows_by_tier["A"]) == 1)
deduped = {tier: dedup_rows(rows) for tier, rows in rows_by_tier.items()}
digest = format_daily_digest(deduped, kw, "日次")
check("ダイジェスト本文にA級タイトルとURLを含む",
      "crazy discovery" in digest and "reddit.com" in digest)
check("ダイジェストにS級の未確認フラグと視聴者価値欄・次のアクションを含む",
      "未確認" in digest and "視聴者価値" in digest and "次のアクション" in digest)
all_ids = [row[0] for rows in rows_by_tier.values() for row in rows]
db.mark_flushed(all_ids)
check("flush後はキュー空",
      all(len(db.pending_digest(tier)) == 0 for tier in MAIL_TIERS))
db.close()
__import__("os").remove("data/test_smoke.db")

print()
print("ALL OK" if not failed else f"FAILED: {len(failed)}")
raise SystemExit(1 if failed else 0)

# トレンド速報ウォッチャー（trend-watcher）

CDPR関連（Cyberpunk 2077 / Orion / Witcher / エッジランナーズS2）の公式発表・リーク・コミュニティの盛り上がりを無人監視し、動画化すべき事象を1日1回のダイジェストメールにまとめて通知する。

> 最上位原則: 速報を出すことではなく「チューマの視聴体験を最速で満たすこと」。通知には必ず「視聴者価値の観点」を含める。速報性と正確性が衝突したら正確性を優先。

要件定義: `../trend_watcher_requirements_v1.md`（フェーズ1実装済み）

## 構成

```
trend-watcher/
├── .github/workflows/watch.yml  # GitHub Actions（15分毎）
├── config/
│   ├── keywords.yaml            # キーワード辞書（S/A/B級・視聴者価値プロンプト）← 人間が随時更新
│   └── sources.yaml             # 監視ソース（RSS / Google News / Reddit）← 人間が随時更新
├── src/                         # Python 3.11+（feedparser, requests, PyYAML）
└── data/watcher.db              # SQLite: 既読・検知履歴・ダイジェストキュー（Actionsがcommitで永続化）
```

## 動作フロー（巡回は15分毎、メール送信は1日1回）

1. RSS（ゲームメディア・Google News検索RSS）とReddit（new.json・認証不要・UA明記・3秒間隔）を巡回
2. 既読チェック（URL正規化+ハッシュで同一URLの重複通知を防止）
3. キーワードマッチ → `スコア = 級の重み × ソース信頼度 × 拡散速度補正（Reddit upvote速度）`
4. スコアでS/A/B級に分類してダイジェストキューに積む（即時送信はしない）
   - B級キーワードでもRedditで急伸していればA級に昇格する
   - r/GamingLeaksAndRumours 由来は必ず「⚠️【未確認】」フラグ付き
   - 同一キーワードのS級は6時間クールダウン（続報はA級として積む）
5. 検知履歴を全件SQLiteに保存（後日の検知精度レビュー用）
6. 前回送信から24時間経過した巡回で、S/A/B級をまとめた1通のダイジェストメールを送信（S級は視聴者価値の観点付きで先頭に表示、A/B級は一覧形式）

## セットアップ（GitHub Actions・月額0円）

1. このディレクトリをGitHubリポジトリとしてpush（public推奨: Actions無制限。privateなら`watch.yml`のcronを20分間隔に）
2. Gmailなど送信元にするメールアカウントで「アプリパスワード」を発行
   - Googleアカウント → セキュリティ → 2段階認証を有効化 → アプリパスワードを生成（16桁）
3. リポジトリの Settings → Secrets and variables → Actions に登録:

   | Secret | 内容 | 必須 |
   |---|---|---|
   | `SMTP_HOST` | SMTPサーバー（Gmailなら `smtp.gmail.com`） | ✅ |
   | `SMTP_PORT` | SMTPポート（Gmailなら `587`） | ✅ |
   | `SMTP_USER` | 送信元アカウント（Gmailアドレス） | ✅ |
   | `SMTP_PASSWORD` | 上記で発行したアプリパスワード | ✅ |
   | `MAIL_TO` | 通知を受け取るメールアドレス | ✅ |
   | `MAIL_FROM` | 送信元表示アドレス（省略時は`SMTP_USER`） | - |
   | `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Reddit OAuth資格情報（下記参照・無料） | 推奨 |

4. Actionsタブ → trend-watch → Run workflow で手動実行して疎通確認
   - 初回実行は「シード」（過去記事を既読登録するだけで通知しない）。2回目以降から検知・通知が始まる

### Redditが403になる場合（重要）

RedditはデータセンターIP（GitHub Actionsランナー含む）からの認証なしアクセスを403でブロックすることがある。コードは `www → old.reddit → RSS` の順で自動フォールバックするが、全滅する環境では**無料のOAuth資格情報**を設定するのが確実:

1. https://www.reddit.com/prefs/apps → 「create another app」→ タイプは **script** を選択
2. 発行された `client_id`（アプリ名の下の文字列）と `secret` を上記Secretsに登録

OAuth設定時は `oauth.reddit.com` 経由（無料枠60リクエスト/分）で最優先に取得する。Reddit全滅時もRSS・Google News監視は動き続ける（重要情報は数分〜数十分でメディアに波及するため実質カバーされる）。

## ローカルでの動作確認

```powershell
cd trend-watcher
$env:PYTHONUTF8 = "1"
pip install -r requirements.txt
python -m src.main --dry-run   # メール送信せず通知内容を標準出力に表示
```

実際にメールを飛ばすテスト:

```powershell
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "your-account@gmail.com"
$env:SMTP_PASSWORD = "アプリパスワード16桁"
$env:MAIL_TO = "your-account@gmail.com"
python -m src.main
```

## 受け入れテスト手順（要件 §10）

1. **1日1回まとめて通知**: `keywords.yaml` のS級に入っている動作確認用キーワード `TW-TEST` を含むスレをテスト用subredditに投稿するか、辞書に現在ニュースに載っている語を一時追加 → 検知はすぐ行われるが、メール送信は前回送信から24時間経過するまで溜められる。確認後 `TW-TEST` は削除してよい
2. **重複通知なし**: 同じ実行をもう一度回しても同一URLが再通知されないこと（`data/watcher.db` の既読管理）
3. **未確認フラグ**: r/GamingLeaksAndRumours 由来の通知に「⚠️【未確認】」が付くこと
4. **yamlのみで運用**: `keywords.yaml` / `sources.yaml` の編集だけで監視対象・辞書を変更できること
5. **月額0円**: GitHub Actions無料枠 + Gmail SMTP（無料）のみ。AI APIの定常呼び出しなし（台本骨子は通知を受けた人間がClaude Proで生成）

## 運用

- キーワード辞書の更新は `config/keywords.yaml` を編集（エッジランナーズS2の新キャラ名は判明次第S/A級に追加）
- 誤検知・見逃しは週次でレビューし辞書に反映。検知履歴はSQLiteの `history` テーブルに全件残る:
  ```powershell
  python -c "import sqlite3; [print(r) for r in sqlite3.connect('data/watcher.db').execute('SELECT detected_at,tier,score,source,title FROM history ORDER BY id DESC LIMIT 20')]"
  ```
- 監視subredditやRSSの追加は `config/sources.yaml` に1エントリ追加するだけ

## フェーズ2以降（未実装）

- CDPR公式サイトのHTML差分監視（公式RSSが見つからない場合の代替。`sources.yaml`にコメントで候補記載）
- Xブリッジ（RSSHub経由の公式アカウント監視）の検証。不安定なら撤退
- エッジランナーズS2辞書の拡充・通知フォーマットの実戦チューニング（8月・S2波前）

# HP提案営業 自動化ツール（hpsales）

ホームページを持っていないお店を Google マップで探して、提案用のサンプルサイトを AI で自動生成し、
公開URL付きの営業メールを作って送るまでを自動化するツールです。

```
① 検索        Google Places API で「浦和 ラーメン」などを検索
   │          → 自前のHPが無い店（未登録 / Instagram等のSNSだけ / 食べログ等のポータルだけ）を抽出
② サイト生成   店名・住所・電話・営業時間・評価・口コミの傾向 → Claude が文章を作成 → HTML化
③ 公開        Netlify に店舗ごとのサンプルサイトを公開（検索エンジンには出さない noindex 設定）
④ メール下書き  Claude が「その店の良さに触れた冒頭文」を書き、料金・送信者情報・配信停止方法を付けて下書き
⑤ 人が確認     review で文面を確認 → approve
⑥ 送信        send --live（1日の上限・送信間隔・配信停止リストを自動チェック）
```

①〜④は `run` コマンド1つで自動実行されます。⑤の承認だけは人が行う設計です（理由は下の「法律まわり」）。

## セットアップ

```bash
cd hp-autosales
pip install -r requirements.txt
cp .env.example .env      # 中身を編集
python -m hpsales demo    # APIキー無しで動作確認（output/ にサイトとメールができる）
```

### 必要なアカウント・キー

| 用途 | 取得先 | 備考 |
|---|---|---|
| 店舗検索・口コミ | Google Cloud で **Places API (New)** を有効化して API キー発行 | 従量課金（月ごとの無料枠あり）。料金は Google Maps Platform の料金ページで確認 |
| 文章生成 | Claude API キー（platform.claude.com） | 任意。無しなら Claude Code に書いてもらう（無料モード） |
| サイト公開 | Netlify の Personal access token | 無料プランで可。または `PREVIEW_BASE_URL` で自前の公開先を指定 |
| メール送信 | Gmail ならアプリパスワード | 独自ドメインのメールの方が迷惑メール判定されにくい |

## 無料で使う（0円モード）

| 部分 | 無料にする方法 |
|---|---|
| 店舗検索（Google） | 無料枠（月1,000回ずつ）の範囲で使う。ツールが月900回で自動停止 |
| 文章作成（Claude） | API キーを入れない → Claude Code（このチャット）に書いてもらう |
| サイト公開 | Netlify の無料プラン（`〇〇.netlify.app` のURL） |
| メール送信 | Gmail（アプリパスワード） |

**Google はクレジットカードの登録が必要**です（無料枠内なら請求は0円）。念のため、
Google Cloud の「APIとサービス → 割り当て」で Places API の1日の上限を **30回** などに下げておくと、
ツールの外でも超過しません（30回×31日＝930回）。今月の利用回数は `python -m hpsales usage` で確認できます。

### 文章を Claude Code に書いてもらう流れ

```bash
python -m hpsales auto                 # 検索。文章待ちの店は output/briefs.json に書き出される
#  ↓ Claude Code に「briefs.json の文章を書いて」と頼む → output/contents.json ができる
python -m hpsales import-content output/contents.json   # サイト化 → 公開
python -m hpsales draft --all          # メール下書き（Claude が書いた件名・冒頭文を使う）
```

`build --all --template` を使えば、Claude を使わず定型文でサイトを作ることもできます。

## 使い方

```bash
# 一括実行：検索 → サイト生成 → 公開 → (メールアドレスが分かる店は) 下書き
python -m hpsales run "さいたま市浦和区 ラーメン"
python -m hpsales run "浦和 美容室" --min-reviews 10

python -m hpsales list                      # 一覧（status 付き）

# メールアドレスの登録（下の「メールアドレス問題」参照）
python -m hpsales export leads.csv          # Excel で email / email_source 列を記入
python -m hpsales import leads.csv
python -m hpsales set-email <slug> info@shop.jp --source "https://www.instagram.com/shop/ のプロフィール"

python -m hpsales draft --all               # 営業メール下書き作成（output/outbox/*.eml にも保存）
python -m hpsales review                    # 下書きを全部表示
python -m hpsales approve <slug> ...        # 承認（--all で全部）
python -m hpsales send                      # ドライラン（送らずに確認）
python -m hpsales send --live               # 実際に送信

python -m hpsales optout shop@example.com   # 「配信停止」の返信が来たら登録 → 以後送らない
python -m hpsales skip <slug> --reason "閉店"
```

ステータスの流れ: `new → built → deployed → drafted → approved → sent`（`skipped` / `opted_out`）

## お店探しの自動化（auto）

検索語を毎回考えなくても、**エリア × 業種** の組み合わせを自動で順番に検索します。

```bash
cp targets.example.json targets.json   # areas（地域）と categories（業種）を編集
python -m hpsales auto --plan          # 今回検索する予定を確認（API料金はかからない）
python -m hpsales auto                 # 検索 → サイト生成 → 公開 → 下書き まで自動
python -m hpsales auto --search-only   # 検索だけ
```

- まだ検索していない組み合わせから順に、1回 `max_queries_per_run` 件（初期値10）ずつ検索します
- すべて検索し終わったら、`requery_days`（初期値60日）経ったものから再検索して新しく出来た店を拾います
- 登録済みの店は詳細情報を取り直さないので、API料金が無駄にかかりません
- 業種の初期値は、HPを持っていないことが多い個人店（ラーメン・定食屋・美容室・整体・学習塾など20種）

毎日自動で回すには `scripts/daily.sh` を cron に登録します（送信まではしません。届いた下書きを `review` → `approve` → `send --live`）。

```
0 7 * * * /path/to/hp-autosales/scripts/daily.sh
```

API料金の目安：1回の `auto` で「検索 最大 10件×3ページ」＋「新しく見つかった店の数だけ詳細取得」。
`max_queries_per_run` を減らせば料金も減ります。

## メールアドレス問題（重要）

**Google の Places API はメールアドレスを返しません。** また、HPが無いお店はそもそもメールアドレスを公開していないことが多いです。
そのため、このツールではアドレスを「お店が自分で公開しているもの」に限って、手で（CSVで）登録する設計にしています。

- 探す場所の例：Instagram のプロフィール、Facebook ページ、ホットペッパー等の掲載ページ、店頭のショップカード
- 登録時は **どこで公開されていたか（email_source）** の記録が必須です
- メールが見つからないお店は、サンプルサイトのURLを持って **電話・Instagram DM・訪問** で提案する方が反応率は高いです
  （`list` で `deployed` のままの店がその対象。サイトはもう出来ているので見せるだけ）

## 法律まわり（送る前に必ず確認）

営業メールは **特定電子メール法** の対象です。このツールは以下を守るように作っていますが、最終的な責任は送信者にあります。

- **送信先**：原則は事前同意が必要。例外として「事業者がWeb等で公表しているアドレス」には送れる
  （ただし「広告メールお断り」等の記載がある場合は送ってはいけない）→ `email_source` に公表元を記録
- **表示義務**：送信者の氏名/名称・住所・連絡先・配信停止の方法 → `.env` の SENDER_* が未設定だと下書き・送信できない
- **配信停止**：申し出があったら以後送らない → `optout` で登録すると二度と送られない
- **送りすぎない**：`DAILY_SEND_LIMIT`（初期値20通/日）と `SEND_INTERVAL_SEC`（60秒）で制限

その他の注意:

- **サンプルサイトはお店の公式サイトではない**ので、ページ上部に「提案用サンプル」の帯を出し、noindex で検索に出ないようにしています。
  断られた店のサイトは Netlify 管理画面から削除してください。契約後は `render_site(..., preview=False)` で帯を消して本番化します。
- **Google の口コミ等の扱い**：Google Maps Platform の利用規約には、取得データのキャッシュ（保存）や、そのデータを元にしたコンテンツ作成に制限があります。
  このツールは口コミ本文をサイトに転載せず、評価はGoogleマップへのリンク付きで表示するだけにしていますが、
  本格運用の前に最新の規約を確認してください。**契約後の本番サイトは、写真・メニュー・紹介文をお店から直接もらって作り直す**のが安全です。
- 料金は `.env` の `PRICE_INITIAL` / `PRICE_MONTHLY` で変更できます（ドメイン代・サーバー代の実費を確認して設定）。

## ファイル構成

```
hpsales/
  finder.py    Google Places API で検索・HP有無の判定
  content.py   Claude でサイト文章・メール冒頭文を生成
  render.py    HTML テンプレートへの埋め込み（配色6種）
  templates/site.html.j2
  deploy.py    Netlify への公開
  mailer.py    メール本文・SMTP送信
  discover.py  エリア×業種から検索語を作り、検索順を決める
  db.py        SQLite（data/leads.db）でリード管理
  cli.py       コマンド
tests/         外部APIをモックした通しテスト（python -m unittest discover -s tests）
```

---
name: site-update
description: ジャングル開拓記サイト（index.html）にコンテンツを追加・更新する。開拓状況のカード追加、数字（敷地面積・伐採本数・メンバー数・オープン時期）の更新、マップのピン追加、お知らせや写真の追加、SNSリンクの設定など。「サイトに〜を載せて」「数字を更新して」と言われたときに使う。
---

# サイト更新（site-update）

画像の「スキルで作る → Webサイト・LP」にあたるスキル。作ったあとは `site-audit` で監査する。

## 0. 先に確認すること
- 載せる事実（数字・日付・場所の名前）がユーザーの言葉で示されているか。**示されていない数字や出来事を作らない**。足りなければ聞く。
- `CLAUDE.md` のデザインのルールと、`index.html` を丸ごと読まないルールに従う。

## 1. 中身を読む
```bash
sed -E 's/data:image[^"'"'"')]{200,}/DATAURI/g' index.html > <scratchpad>/stripped.html
```
`stripped.html` で構造を確認し、`index.html` の編集は Edit で該当箇所だけ行う。

## 2. 種類ごとの書き方（既存の部品を使う）

| やりたいこと | 場所 | 部品 |
|---|---|---|
| 開拓中のエリアを増やす | `.sec-jungle` の `.board-grid` | `<div class="wood-board"><h3>絵文字 名前</h3><p>2〜3文の説明</p></div>` |
| 数字を更新・追加 | `.stat-strip` | `<div class="stat-leaf"><b>値</b><span>ラベル</span></div>`（最大5個） |
| マップに場所を足す | `.area-map` | `<div class="area-pin" style="top:%; left:%;">名前</div>`（他のピンと重ならない位置） |
| ヒーローの標識 | `.hero` 内 | `<div class="arrow-sign a4">文言</div>` と `.a4{top/left/rotate}` を追加 |
| 新しいセクション（お知らせ・写真など） | `</footer>` の前 | `<section><div class="section-head"><span class="tag">絵文字 ラベル</span><h2>見出し</h2></div>…</section>` |
| SNS・お問い合わせ | `footer .socials` | `<span>` を `<a href="…" target="_blank" rel="noopener">` に置き換える（URL はユーザーに確認） |

- `.board-grid` は3列。カードが3の倍数でなくなるときはレイアウトをユーザーに相談する。
- 写真を足すときは、幅1600px以下に縮小して JPEG（品質80前後）にし、data URI で埋め込む。`alt` を必ず付ける。
  ```bash
  python3 -c "from PIL import Image;im=Image.open('in.jpg');im.thumbnail((1600,1600));im.convert('RGB').save('out.jpg',quality=80)"
  base64 -w0 out.jpg
  ```
- 文章は CLAUDE.md のトーン（手づくり感・冒険感、一人称、誇張しない）で書く。

## 3. 仕上げ
1. 編集のたびに PostToolUse フック（`.claude/hooks/check-html.py`）が自動チェックする。エラーが出たら直す。
2. `site-audit` スキルで監査し、スクリーンショットを確認する。
3. 何を変えたかを短くまとめて commit する（例：`開拓状況に「トイレ」カードを追加`）。

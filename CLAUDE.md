# URAYAMA JUNGLE FIELD（ジャングル開拓記 / 浦山銀路）

キャンプ場「URAYAMA JUNGLE FIELD」の開拓記サイト。運営・開拓者は浦山銀路（@ginji.campfield）。
2026年夏のプレオープンに向けて、密林をゼロから切り拓いていく過程を発信する。

## 読者とトーン
- 読者：キャンプ好き、開拓の様子を応援してくれる人、将来の利用者。
- トーン：手づくり感・冒険感。「開拓」「密林」「切り拓く」などの言葉を軸に、親しみやすい一人称で書く。
- 誇張しない。数字（面積・伐採本数・メンバー数・オープン時期）は実際の値だけを書く。分からない値は推測で埋めず、本人に確認する。

## ファイル構成
- `index.html` 1ファイルで完結させる（CSS は `<style>` に、画像は data URI で埋め込み）。外部 CSS / JS / フレームワークは足さない。
- ヒーロー背景画像は base64 で埋め込まれており、ファイルが約800KBある。
  **`index.html` を Read / cat でそのまま全部読まない**。中身を読むときは画像部分を潰したコピーを使う：
  `sed -E 's/data:image[^"'"'"')]{200,}/DATAURI/g' index.html > <scratchpad>/stripped.html`
- 編集は Edit で該当箇所だけを書き換える（data URI を含む行を書き直さない）。
- 新しい画像を埋め込むときは、幅 1600px 以下・JPEG/WebP に圧縮してから。`index.html` 全体で 2MB を超えないようにする。
- 例外：SNS シェア用画像 `og-image.jpg`（1200×630）だけは別ファイル（OGP は data URI を読めないため）。ヒーローの見出しや背景を変えたら作り直す。公開物に含めるファイルは `.github/workflows/pages.yml` の `cp` 行で管理する。
- 公開 URL：https://gin8ten-svg.github.io/urayama-jungle-field/ （`canonical` / `og:url` / `og:image` もこれに合わせる）

## デザインのルール
- 色は `:root` の変数だけを使う。新色を足すときは変数として追加する。
  - `--deep #12200F` 背景 / `--green` `--green2` 森 / `--lime #C6E85A` 強調・見出し / `--lime2` 補助
  - `--mud` `--mud-dark` 木の看板 / `--paper #F0EBDD` 本文 / `--red #C4462C` 標識・ピン / `--ink` 枠線
- 見出し・ロゴは `.carved`（丸ゴシック 900）。
- 部品は既存のものを再利用する：`.sign`（看板）、`.arrow-sign`（矢印標識）、`.wood-board`（木の板カード）、`.stat-leaf`（数字の葉っぱ）、`.area-pin`（地図ピン）、`.section-head .tag`（セクションのラベル）。
- 少し傾ける（rotate ±2〜6deg）・黒い影（box-shadow のずらし）で「手づくりの看板」らしさを出す。
- 新しいアニメーションは `@media(prefers-reduced-motion: reduce)` で止める。
- スマホ（幅 720px 以下）でも崩れないこと。`@media(max-width:720px)` に対応を書く。

## 作業の流れ
1. 変更は Edit で最小限に。
2. 変更後は `/site-audit`（`.claude/skills/site-audit`）でパソコン幅・スマホ幅のスクリーンショットと自動チェックを行い、結果を見てから commit する。
3. コンテンツの追加（お知らせ・開拓状況・写真・数字の更新）は `/site-update` の手順に沿う。
4. 下書きや写真が Google ドライブにある場合は `/drive-to-site` を使う。
5. `main` に入ると GitHub Pages に自動公開される（`.github/workflows/pages.yml`）。main へ直接 push しない。必ずブランチ → PR。

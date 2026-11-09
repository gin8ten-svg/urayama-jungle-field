// サイト監査：パソコン幅・スマホ幅でスクリーンショットを撮り、よくある問題を自動チェックする。
// 使い方: node .claude/skills/site-audit/scripts/audit.mjs [index.html] [出力先ディレクトリ]
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium, devices } = require('playwright');

const target = path.resolve(process.argv[2] || 'index.html');
const outDir = path.resolve(process.argv[3] || 'audit-output');
fs.mkdirSync(outDir, { recursive: true });

const html = fs.readFileSync(target, 'utf8');
const head = html.slice(0, 4000) + html.replace(/data:[^"')]+/g, '').slice(0, 20000);
const findings = [];
const add = (level, msg) => findings.push({ level, msg });

// --- 静的チェック ---
const sizeMB = fs.statSync(target).size / 1024 / 1024;
add(sizeMB > 2 ? 'NG' : sizeMB > 1 ? '注意' : 'OK', `ファイルサイズ ${sizeMB.toFixed(2)}MB`);
if (!/^\s*<!doctype html>/i.test(html)) add('NG', '<!doctype html> がない（古い表示モードで描画される）');
if (!/<html[^>]*\blang=/i.test(head)) add('注意', '<html lang="ja"> がない');
if (!/<meta[^>]+charset/i.test(head)) add('NG', '<meta charset="utf-8"> がない（文字化けの恐れ）');
if (!/<meta[^>]+name=["']viewport/i.test(head)) add('NG', 'viewport の meta がない（スマホで縮小表示される）');
if (!/<meta[^>]+name=["']description/i.test(head)) add('注意', 'meta description がない（検索結果の説明文）');
for (const p of ['og:title', 'og:description', 'og:image', 'og:url'])
  if (!new RegExp(`property=["']${p}`, 'i').test(head)) add('注意', `${p} がない（SNSでシェアしたときの表示）`);
if (!/rel=["']icon/i.test(head)) add('注意', 'favicon がない');

// --- ブラウザでのチェック ---
const browser = await chromium.launch();
const url = pathToFileURL(target).href;
const shots = [
  { name: 'desktop', opts: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 } },
  { name: 'mobile', opts: { ...devices['iPhone 13'] } },
];
for (const s of shots) {
  const ctx = await browser.newContext({ ...s.opts, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => m.type() === 'error' && errors.push(m.text()));
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(outDir, `${s.name}-top.png`) });
  await page.screenshot({ path: path.join(outDir, `${s.name}-full.png`), fullPage: true });

  const r = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const over = [];
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.display === 'none') continue;
      if (el.closest('.foliage, .trunks, svg')) continue; // 装飾は対象外
      const b = el.getBoundingClientRect();
      if (b.width && (b.right > vw + 1 || b.left < -1)) {
        over.push(`${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''}「${(el.textContent || '').trim().slice(0, 15)}」`);
      }
    }
    const small = [...document.querySelectorAll('p, span, a, li, b, h1, h2, h3, div')]
      .filter(el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
      .filter(el => parseFloat(getComputedStyle(el).fontSize) < 11)
      .map(el => `${(el.textContent || '').trim().slice(0, 12)}(${getComputedStyle(el).fontSize})`);
    const deadLinks = [...document.querySelectorAll('[tabindex], a')]
      .filter(el => el.tagName !== 'A' || !el.getAttribute('href') || el.getAttribute('href') === '#')
      .map(el => (el.textContent || '').trim());
    const imgsNoAlt = [...document.querySelectorAll('img:not([alt])')].length;
    return { vw, scrollW: document.documentElement.scrollWidth, over: over.slice(0, 10), small: [...new Set(small)].slice(0, 10), deadLinks, imgsNoAlt, h1: document.querySelectorAll('h1').length };
  });

  const tag = s.name === 'desktop' ? 'PC' : 'スマホ';
  if (r.scrollW > r.vw + 1) add('NG', `${tag}: 横スクロールが発生（ページ幅 ${r.scrollW}px > 画面 ${r.vw}px）`);
  if (r.over.length) add('注意', `${tag}: 画面からはみ出している要素: ${r.over.join(', ')}`);
  if (r.small.length) add('注意', `${tag}: 11px未満の小さい文字: ${r.small.join(', ')}`);
  if (s.name === 'desktop') {
    if (r.deadLinks.length) add('注意', `リンク先がない（クリックしても何も起きない）: ${r.deadLinks.join(', ')}`);
    if (r.imgsNoAlt) add('注意', `alt のない画像 ${r.imgsNoAlt} 枚`);
    if (r.h1 !== 1) add('注意', `h1 が ${r.h1} 個（1個が望ましい）`);
  }
  if (errors.length) add('NG', `${tag}: JavaScript エラー: ${errors.join(' / ')}`);
  await ctx.close();
}
await browser.close();

const order = { NG: 0, '注意': 1, OK: 2 };
findings.sort((a, b) => order[a.level] - order[b.level]);
const report = [
  `# サイト監査レポート（${new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC）`,
  '',
  `対象: ${path.relative(process.cwd(), target)}`,
  '',
  ...findings.map(f => `- **${f.level}** ${f.msg}`),
  '',
  `スクリーンショット: ${shots.flatMap(s => [`${s.name}-top.png`, `${s.name}-full.png`]).join(', ')}`,
].join('\n');
fs.writeFileSync(path.join(outDir, 'report.md'), report);
console.log(report);
console.log(`\n出力先: ${outDir}`);
process.exitCode = findings.some(f => f.level === 'NG') ? 1 : 0;

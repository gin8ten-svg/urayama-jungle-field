#!/usr/bin/env python3
"""PostToolUse hook: index.html を編集したあとに壊れていないかを自動チェックする。

- 対象は *.html の編集だけ。それ以外は何もせず終了する。
- 問題があれば exit 2 で stderr に書き、Claude に直させる。
- 単体でも使える: python3 .claude/hooks/check-html.py index.html
"""
import json
import os
import re
import sys
from html.parser import HTMLParser

MAX_BYTES = 2 * 1024 * 1024  # 2MB
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr", "path", "polygon", "ellipse",
        "circle", "rect", "line", "polyline", "stop", "use"}
TRACK = {"div", "section", "header", "footer", "main", "nav", "article",
         "aside", "ul", "ol", "li", "p", "a", "span", "h1", "h2", "h3",
         "h4", "svg", "g", "style", "script", "button", "figure", "table"}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.style_text = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self._in_style = True
        if tag in TRACK and tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))
        for name, val in attrs:
            if name == "style" and val and val.count("{"):
                self.errors.append(f"{self.getpos()[0]}行目: style属性に '{{' が入っています")

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag not in TRACK or tag in VOID:
            return
        # p / li は暗黙に閉じられることがあるので寛容に扱う
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                unclosed = [t for t in self.stack[i + 1:] if t[0] not in ("p", "li")]
                for t, line in unclosed:
                    self.errors.append(f"{line}行目: <{t}> が閉じられていません（</{tag}> の前）")
                del self.stack[i:]
                return
        self.errors.append(f"{self.getpos()[0]}行目: 対応する開きタグのない </{tag}>")

    def handle_data(self, data):
        if self._in_style:
            self.style_text.append(data)


def check(path):
    problems = []
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        problems.append(f"ファイルサイズが {size/1024/1024:.2f}MB です（上限 2MB）。画像を圧縮してください。")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    c = Checker()
    c.feed(text)
    c.close()
    problems += c.errors
    for t, line in c.stack:
        if t not in ("p", "li"):
            problems.append(f"{line}行目: <{t}> がファイル末尾まで閉じられていません")
    css = re.sub(r"/\*.*?\*/", "", "".join(c.style_text), flags=re.S)
    css = re.sub(r"url\((\"|')?data:[^)]*\)", "url()", css)
    if css.count("{") != css.count("}"):
        problems.append(f"<style> の波かっこが合っていません（{{ {css.count('{')} 個 / }} {css.count('}')} 個）")
    used = set(re.findall(r"var\((--[\w-]+)\)", text))
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    for v in sorted(used - defined):
        problems.append(f"未定義のCSS変数 {v} が使われています（:root に追加してください）")
    return problems


def main():
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0
        p = (payload.get("tool_input") or {}).get("file_path", "")
        if not p.endswith(".html") or not os.path.exists(p):
            return 0
        paths = [p]
    failed = False
    for p in paths:
        probs = check(p)
        if probs:
            failed = True
            print(f"[check-html] {p} に問題があります:", file=sys.stderr)
            for x in probs[:20]:
                print(f"  - {x}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

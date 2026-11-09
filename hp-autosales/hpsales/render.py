"""サイト文章 + 店舗情報 → 静的 HTML を出力する。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings

PALETTES = {
    "warm":    "--main:#c2410c; --accent:#f59e0b; --ink:#2b2118; --bg:#fffaf3; --soft:#fdf0e1; --card:#ffffff;",
    "fresh":   "--main:#0e7490; --accent:#22c55e; --ink:#13232b; --bg:#f7fdfd; --soft:#e6f6f6; --card:#ffffff;",
    "natural": "--main:#4d7c0f; --accent:#a16207; --ink:#1f2a17; --bg:#fbfbf4; --soft:#eef2e2; --card:#ffffff;",
    "elegant": "--main:#7c2d12; --accent:#1e293b; --ink:#1c1917; --bg:#faf8f5; --soft:#f0ebe4; --card:#ffffff;",
    "pop":     "--main:#db2777; --accent:#f97316; --ink:#2a1420; --bg:#fff8fb; --soft:#fde7f1; --card:#ffffff;",
    "mono":    "--main:#262626; --accent:#737373; --ink:#171717; --bg:#fafafa; --soft:#efefef; --card:#ffffff;",
}

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_site(lead: dict[str, Any], content: dict[str, Any], preview: bool = True) -> str:
    place = lead.get("place_json") or {}
    if isinstance(place, str):
        place = json.loads(place or "{}")
    hours = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions", [])
    return _env.get_template("site.html.j2").render(
        shop=lead,
        c=content,
        palette=PALETTES.get(content.get("theme"), PALETTES["warm"]),
        hours=hours,
        map_query=f"{lead['name']} {lead.get('address', '')}",
        preview=preview,
        year=date.today().year,
    )


def write_site(slug: str, html: str, content: dict[str, Any]) -> Path:
    out = settings.sites_dir / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return out / "index.html"

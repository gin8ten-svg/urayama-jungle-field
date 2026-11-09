"""お店探しの自動化：エリア × 業種 の組み合わせから検索語を作り、
まだ調べていないもの → 前回から日数が経ったもの の順に検索する。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT

DEFAULTS: dict[str, Any] = {
    "areas": [],
    # HP を持っていないことが多い個人店の業種
    "categories": [
        "ラーメン", "定食屋", "居酒屋", "焼き鳥", "そば うどん", "喫茶店", "カフェ", "パン屋",
        "洋菓子店", "和菓子店", "美容室", "理容室", "ネイルサロン", "整体", "鍼灸院",
        "学習塾", "花屋", "クリーニング", "自転車店", "リフォーム 工務店",
    ],
    "max_queries_per_run": 10,   # 1回の実行で検索する件数（API料金の上限になる）
    "requery_days": 60,          # 同じ検索語を再検索するまでの日数
    "max_results": 60,
    "min_reviews": 5,
    "include_sns": True,
}


def load_targets(path: Path | None = None) -> dict[str, Any]:
    path = path or (ROOT / "targets.json")
    if not path.exists():
        path = ROOT / "targets.example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = {**DEFAULTS, **data}
    if not targets["areas"]:
        raise SystemExit(f"{path} の areas が空です。探したい地域を入れてください")
    return targets


def all_queries(targets: dict[str, Any]) -> list[str]:
    return [f"{area} {cat}" for area in targets["areas"] for cat in targets["categories"]]


def plan(targets: dict[str, Any], history: dict[str, str], limit: int | None = None,
         now: datetime | None = None) -> list[str]:
    """今回検索する検索語を選ぶ。未実行を優先し、次に古い順。"""
    now = now or datetime.now(timezone.utc)
    limit = targets["max_queries_per_run"] if limit is None else limit
    cutoff = now - timedelta(days=targets["requery_days"])
    fresh = [q for q in all_queries(targets) if q not in history]
    stale = sorted(
        (q for q in all_queries(targets)
         if q in history and datetime.fromisoformat(history[q]) < cutoff),
        key=lambda q: history[q],
    )
    return (fresh + stale)[:limit]

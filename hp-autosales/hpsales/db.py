"""見込み客（リード）を SQLite で管理する。

ステータスの流れ:
  new → built → deployed → drafted → approved → sent
  （途中で skipped / opted_out になることがある）
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    place_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    address         TEXT,
    phone           TEXT,
    rating          REAL,
    review_count    INTEGER,
    maps_url        TEXT,
    website         TEXT,
    web_status      TEXT,          -- none / sns_only / portal_only
    place_json      TEXT,          -- Places API の生データ
    query           TEXT,
    email           TEXT,
    email_source    TEXT,          -- メールアドレスを確認した公開場所（特電法の記録用）
    slug            TEXT,
    site_path       TEXT,
    preview_url     TEXT,
    netlify_site_id TEXT,
    content_json    TEXT,          -- サイト文章とメール冒頭文（Claude が作成）
    email_subject   TEXT,
    email_body      TEXT,
    status          TEXT NOT NULL DEFAULT 'new',
    note            TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    sent_at         TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
    month   TEXT,
    sku     TEXT,
    count   INTEGER,
    PRIMARY KEY (month, sku)
);
CREATE TABLE IF NOT EXISTS search_log (
    query       TEXT PRIMARY KEY,
    last_run    TEXT,
    found       INTEGER
);
CREATE TABLE IF NOT EXISTS suppression (
    email       TEXT PRIMARY KEY,
    reason      TEXT,
    created_at  TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
    for col in ("netlify_site_id", "content_json"):  # 古いDBへの列追加
        if col not in cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    return conn


def upsert_lead(conn: sqlite3.Connection, lead: dict[str, Any]) -> bool:
    """新規なら True。既存リードは基本情報だけ更新し、進捗は保持する。"""
    exists = conn.execute("SELECT 1 FROM leads WHERE place_id=?", (lead["place_id"],)).fetchone()
    lead = {**lead, "place_json": json.dumps(lead.get("place_json", {}), ensure_ascii=False)}
    if exists:
        cols = ["name", "category", "address", "phone", "rating", "review_count",
                "maps_url", "website", "web_status", "place_json"]
        conn.execute(
            f"UPDATE leads SET {', '.join(f'{c}=?' for c in cols)}, updated_at=? WHERE place_id=?",
            [lead.get(c) for c in cols] + [now(), lead["place_id"]],
        )
        conn.commit()
        return False
    lead.setdefault("status", "new")
    lead["created_at"] = lead["updated_at"] = now()
    keys = list(lead.keys())
    conn.execute(
        f"INSERT INTO leads ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
        [lead[k] for k in keys],
    )
    conn.commit()
    return True


def update(conn: sqlite3.Connection, place_id: str, **fields: Any) -> None:
    fields["updated_at"] = now()
    conn.execute(
        f"UPDATE leads SET {', '.join(f'{k}=?' for k in fields)} WHERE place_id=?",
        list(fields.values()) + [place_id],
    )
    conn.commit()


def get(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """place_id または slug で 1 件取得。"""
    return conn.execute("SELECT * FROM leads WHERE place_id=? OR slug=?", (key, key)).fetchone()


def by_status(conn: sqlite3.Connection, statuses: Iterable[str]) -> list[sqlite3.Row]:
    statuses = list(statuses)
    return conn.execute(
        f"SELECT * FROM leads WHERE status IN ({', '.join('?' for _ in statuses)}) ORDER BY created_at",
        statuses,
    ).fetchall()


def all_leads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM leads ORDER BY created_at").fetchall()


def is_suppressed(conn: sqlite3.Connection, email: str) -> bool:
    return conn.execute("SELECT 1 FROM suppression WHERE email=?", (email.lower(),)).fetchone() is not None


def suppress(conn: sqlite3.Connection, email: str, reason: str = "opt-out") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO suppression (email, reason, created_at) VALUES (?, ?, ?)",
        (email.lower(), reason, now()),
    )
    conn.execute("UPDATE leads SET status='opted_out', updated_at=? WHERE lower(email)=?", (now(), email.lower()))
    conn.commit()


def usage(conn: sqlite3.Connection, sku: str) -> int:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute("SELECT count FROM api_usage WHERE month=? AND sku=?", (month, sku)).fetchone()
    return row[0] if row else 0


def consume(conn: sqlite3.Connection, sku: str, limit: int) -> bool:
    """1回分を記録する。上限に達していたら記録せず False。"""
    if usage(conn, sku) >= limit:
        return False
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn.execute(
        "INSERT INTO api_usage (month, sku, count) VALUES (?, ?, 1) "
        "ON CONFLICT(month, sku) DO UPDATE SET count = count + 1",
        (month, sku),
    )
    conn.commit()
    return True


def known_place_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT place_id FROM leads")}


def log_search(conn: sqlite3.Connection, query: str, found: int) -> None:
    conn.execute("INSERT OR REPLACE INTO search_log (query, last_run, found) VALUES (?, ?, ?)",
                 (query, now(), found))
    conn.commit()


def search_history(conn: sqlite3.Connection) -> dict[str, str]:
    """query → 最終実行日時"""
    return {r["query"]: r["last_run"] for r in conn.execute("SELECT query, last_run FROM search_log")}


def sent_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM leads WHERE sent_at LIKE ?", (f"{today}%",)).fetchone()[0]

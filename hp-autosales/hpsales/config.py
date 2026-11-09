"""設定の読み込み。.env ファイル（KEY=VALUE 形式）と環境変数から読む。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class Settings:
    google_maps_api_key: str = field(default_factory=lambda: _env("GOOGLE_MAPS_API_KEY"))
    # 無料枠を超えないための月間上限（Google の無料枠は SKU ごとに月1,000回）
    places_search_monthly_limit: int = field(default_factory=lambda: int(_env("PLACES_SEARCH_MONTHLY_LIMIT", "900")))
    places_details_monthly_limit: int = field(default_factory=lambda: int(_env("PLACES_DETAILS_MONTHLY_LIMIT", "900")))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-opus-5-5"))

    # 送信者情報（特定電子メール法で表示が義務付けられている項目）
    sender_name: str = field(default_factory=lambda: _env("SENDER_NAME"))
    sender_company: str = field(default_factory=lambda: _env("SENDER_COMPANY"))
    sender_address: str = field(default_factory=lambda: _env("SENDER_ADDRESS"))
    sender_phone: str = field(default_factory=lambda: _env("SENDER_PHONE"))
    sender_email: str = field(default_factory=lambda: _env("SENDER_EMAIL"))
    optout_email: str = field(default_factory=lambda: _env("OPTOUT_EMAIL") or _env("SENDER_EMAIL"))

    # 販売条件
    price_initial: str = field(default_factory=lambda: _env("PRICE_INITIAL", "29,800円（税込）"))
    price_monthly: str = field(default_factory=lambda: _env("PRICE_MONTHLY", "月額 980円（サーバー・ドメイン・軽微な更新込み）"))

    # SMTP
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    daily_send_limit: int = field(default_factory=lambda: int(_env("DAILY_SEND_LIMIT", "20")))
    send_interval_sec: int = field(default_factory=lambda: int(_env("SEND_INTERVAL_SEC", "60")))

    # 公開（Netlify）
    netlify_token: str = field(default_factory=lambda: _env("NETLIFY_TOKEN"))
    preview_base_url: str = field(default_factory=lambda: _env("PREVIEW_BASE_URL"))

    db_path: Path = field(default_factory=lambda: ROOT / _env("DB_PATH", "data/leads.db"))
    briefs_path: Path = field(default_factory=lambda: ROOT / "output" / "briefs.json")
    sites_dir: Path = field(default_factory=lambda: ROOT / "output" / "sites")
    outbox_dir: Path = field(default_factory=lambda: ROOT / "output" / "outbox")

    def missing_sender_fields(self) -> list[str]:
        required = {
            "SENDER_NAME": self.sender_name,
            "SENDER_ADDRESS": self.sender_address,
            "SENDER_EMAIL": self.sender_email,
        }
        return [k for k, v in required.items() if not v]


settings = Settings()

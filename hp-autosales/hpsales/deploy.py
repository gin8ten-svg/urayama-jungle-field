"""提案用サンプルサイトを公開してURLを得る。

方法は2通り:
- NETLIFY_TOKEN がある → Netlify に店舗ごとのサイトを作って ZIP デプロイ（無料枠で可）
- PREVIEW_BASE_URL がある → output/sites/ を自分で公開している前提で URL を組み立てる
  （例: GitHub Pages / Cloudflare Pages に output/sites を置く）
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import requests

from .config import settings

NETLIFY_API = "https://api.netlify.com/api/v1"


def _zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in path.rglob("*"):
            if f.is_file() and f.name != "content.json":
                zf.write(f, f.relative_to(path))
    return buf.getvalue()


def deploy_netlify(slug: str, site_dir: Path, site_id: str | None = None) -> tuple[str, str]:
    """(site_id, url) を返す。"""
    headers = {"Authorization": f"Bearer {settings.netlify_token}"}
    if not site_id:
        r = requests.post(f"{NETLIFY_API}/sites", json={"name": f"demo-{slug}"[:60]}, headers=headers, timeout=30)
        if r.status_code == 422:  # 名前の重複 → 自動命名に任せる
            r = requests.post(f"{NETLIFY_API}/sites", json={}, headers=headers, timeout=30)
        r.raise_for_status()
        site = r.json()
        site_id = site["id"]
    r = requests.post(
        f"{NETLIFY_API}/sites/{site_id}/deploys",
        data=_zip_dir(site_dir),
        headers={**headers, "Content-Type": "application/zip"},
        timeout=120,
    )
    r.raise_for_status()
    deploy = r.json()
    url = deploy.get("ssl_url") or deploy.get("url") or f"https://{deploy.get('subdomain')}.netlify.app"
    return site_id, url


def publish(slug: str, site_dir: Path, netlify_site_id: str | None = None) -> tuple[str, str | None]:
    """(preview_url, netlify_site_id) を返す。再デプロイ時は同じ Netlify サイトを使う。"""
    if settings.netlify_token:
        site_id, url = deploy_netlify(slug, site_dir, netlify_site_id)
        return url, site_id
    if settings.preview_base_url:
        return settings.preview_base_url.rstrip("/") + f"/{slug}/", None
    raise SystemExit("公開先が未設定です。.env に NETLIFY_TOKEN か PREVIEW_BASE_URL を設定してください")

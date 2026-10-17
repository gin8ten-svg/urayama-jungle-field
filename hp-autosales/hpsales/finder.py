"""Google Places API (New) で「自前のホームページを持っていない店」を探す。

1. Text Search で候補を取得（websiteUri を含めて取得）
2. websiteUri が空、または SNS / グルメサイトのみの店を残す
3. 残った店だけ Place Details で口コミ・営業時間などの詳細を取得（課金を抑えるため）
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from .config import settings

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

SEARCH_FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.websiteUri",
    "places.businessStatus", "places.rating", "places.userRatingCount", "nextPageToken",
])
DETAIL_FIELDS = ",".join([
    "id", "displayName", "formattedAddress", "shortFormattedAddress", "nationalPhoneNumber",
    "websiteUri", "rating", "userRatingCount", "googleMapsUri", "primaryType",
    "primaryTypeDisplayName", "regularOpeningHours", "priceLevel", "editorialSummary",
    "reviews", "location", "businessStatus",
])

# 「HPはないが SNS やポータルだけある」店も見込み客として扱う
SNS_DOMAINS = ("instagram.com", "facebook.com", "twitter.com", "x.com", "tiktok.com",
               "line.me", "lin.ee", "ameblo.jp", "linktr.ee", "lit.link", "note.com", "youtube.com")
PORTAL_DOMAINS = ("tabelog.com", "hotpepper.jp", "gnavi.co.jp", "retty.me", "ikyu.com",
                  "beauty.hotpepper.jp", "epark.jp", "minimodel.jp", "rakuten.co.jp",
                  "google.com", "business.site", "g.page")


def classify_website(url: str | None) -> str:
    """none / sns_only / portal_only / own を返す。own 以外が営業対象。"""
    if not url:
        return "none"
    host = (urlparse(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in SNS_DOMAINS):
        return "sns_only"
    if any(host == d or host.endswith("." + d) for d in PORTAL_DOMAINS):
        return "portal_only"
    return "own"


HINTS = {
    "billing": "このプロジェクトに請求先アカウントがリンクされていません（Google Cloud →「お支払い」→ 請求先アカウントをリンク）",
    "has not been used": "このプロジェクトで Places API (New) が有効になっていません",
    "is disabled": "このプロジェクトで Places API (New) が有効になっていません",
    "blocked": "APIキーの「APIの制限」で Places API (New) が許可されていません",
    "API key not valid": "APIキーが間違っています（.env の GOOGLE_MAPS_API_KEY を確認）",
    "referer": "APIキーに「ウェブサイトの制限」が付いています（アプリケーションの制限を「なし」に）",
    "IP address": "APIキーに「IPアドレスの制限」が付いています（アプリケーションの制限を「なし」に）",
}


def _check(r: requests.Response) -> None:
    """エラー時に Google からの理由と、日本語の対処法を出す。"""
    if r.ok:
        return
    try:
        message = r.json().get("error", {}).get("message", "")
    except ValueError:
        message = r.text[:300]
    hint = next((h for k, h in HINTS.items() if k.lower() in message.lower()), "")
    raise RuntimeError(f"Google API エラー {r.status_code}: {message}" + (f"\n  → {hint}" if hint else ""))


def _headers(fields: str) -> dict[str, str]:
    if not settings.google_maps_api_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY が設定されていません（.env を確認してください）")
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": fields,
    }


class QuotaExceeded(Exception):
    """無料枠を守るための月間上限に達した"""


def _noop_guard(sku: str) -> None:
    pass


def search(query: str, max_results: int = 60, guard=_noop_guard) -> list[dict[str, Any]]:
    """Text Search（1ページ最大20件、最大3ページ）"""
    results: list[dict[str, Any]] = []
    body: dict[str, Any] = {"textQuery": query, "languageCode": "ja", "regionCode": "JP", "pageSize": 20}
    while len(results) < max_results:
        guard("search")
        r = requests.post(SEARCH_URL, json=body, headers=_headers(SEARCH_FIELDS), timeout=30)
        _check(r)
        data = r.json()
        results.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(1)
    return results[:max_results]


def details(place_id: str, guard=_noop_guard) -> dict[str, Any]:
    guard("details")
    r = requests.get(
        DETAILS_URL.format(place_id=place_id),
        params={"languageCode": "ja", "regionCode": "JP"},
        headers=_headers(DETAIL_FIELDS),
        timeout=30,
    )
    _check(r)
    return r.json()


def to_lead(place: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "place_id": place["id"],
        "name": place.get("displayName", {}).get("text", ""),
        "category": place.get("primaryTypeDisplayName", {}).get("text", ""),
        "address": re.sub(r"^日本、\s*", "", place.get("formattedAddress", "")),
        "phone": place.get("nationalPhoneNumber", ""),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "maps_url": place.get("googleMapsUri", ""),
        "website": place.get("websiteUri", ""),
        "web_status": classify_website(place.get("websiteUri")),
        "place_json": place,
        "query": query,
    }


def find_leads(query: str, max_results: int = 60, min_reviews: int = 5,
               include_sns: bool = True, skip_ids: set[str] | None = None,
               guard=_noop_guard) -> list[dict[str, Any]]:
    """営業対象になりそうな店を返す。口コミが少なすぎる店（休眠・新規すぎ）は除外。
    skip_ids（登録済みの店）は詳細取得を省いて API 料金を節約する。"""
    targets = {"none", "sns_only", "portal_only"} if include_sns else {"none"}
    skip_ids = skip_ids or set()
    leads = []
    try:
        found = search(query, max_results, guard)
    except QuotaExceeded as e:
        e.partial = []
        raise
    for p in found:
        if p["id"] in skip_ids:
            continue
        if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        if classify_website(p.get("websiteUri")) not in targets:
            continue
        if (p.get("userRatingCount") or 0) < min_reviews:
            continue
        try:
            leads.append(to_lead(details(p["id"], guard), query))
        except QuotaExceeded as e:
            e.partial = leads
            raise
    return leads


def slugify(name: str, place_id: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    suffix = re.sub(r"[^a-z0-9]", "", place_id.lower())[-6:]
    return f"{ascii_part[:30]}-{suffix}".strip("-") if ascii_part else f"shop-{suffix}"

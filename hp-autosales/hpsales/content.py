"""Claude API でサイトの文章と営業メール本文を生成する。

ANTHROPIC_API_KEY（または `ant auth login` の認証情報）が無い場合は、
テンプレート文章にフォールバックする（動作確認用）。
"""
from __future__ import annotations

import json
import os
from typing import Any

from .config import settings

THEMES = ["warm", "fresh", "natural", "elegant", "pop", "mono"]

SITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "catchcopy": {"type": "string", "description": "ヒーローに出す20字前後のキャッチコピー"},
        "lead": {"type": "string", "description": "キャッチコピー下の60〜100字の紹介文"},
        "about": {"type": "string", "description": "お店紹介 200〜300字。改行は\\nで"},
        "features": {
            "type": "array",
            "description": "お店の魅力3つ",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                "required": ["title", "text"],
                "additionalProperties": False,
            },
        },
        "recommended": {
            "type": "array",
            "description": "口コミ等で言及されている人気メニュー/サービス（最大4件、根拠が無ければ空配列）",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "text": {"type": "string"}},
                "required": ["name", "text"],
                "additionalProperties": False,
            },
        },
        "scenes": {
            "type": "array",
            "description": "おすすめの利用シーン 3つ（例: 一人ランチ, 記念日）",
            "items": {"type": "string"},
        },
        "theme": {"type": "string", "enum": THEMES, "description": "お店の雰囲気に合う配色"},
        "hero_emoji": {"type": "string", "description": "ヒーローに添える絵文字1文字"},
        "meta_description": {"type": "string", "description": "検索結果用の説明 120字以内"},
    },
    "required": ["catchcopy", "lead", "about", "features", "recommended", "scenes",
                 "theme", "hero_emoji", "meta_description"],
    "additionalProperties": False,
}

EMAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "opening": {"type": "string", "description": "お店の具体的な良さに触れる冒頭2〜3文"},
    },
    "required": ["subject", "opening"],
    "additionalProperties": False,
}

SITE_SYSTEM = """あなたは小規模店舗のホームページを作るプロのコピーライターです。
与えられたお店の公開情報と、お客さんの声の傾向をもとに、お店自身の言葉としてサイトの文章を書きます。

守ること:
- 口コミの文章をそのまま引用・転載しない。傾向を読み取り、自分の言葉で書く。
- 情報に無い事実（価格、メニュー名、創業年、受賞歴、こだわりの産地など）を作らない。
  根拠が弱いところは「〜を大切にしています」のような雰囲気の表現にとどめる。
- ネガティブな口コミの内容には触れない。
- 誇大表現（日本一、絶対、No.1 等）は使わない。
- 自然で温かい日本語。お客さんに語りかける口調。"""

EMAIL_SYSTEM = """あなたは小さなお店向けにホームページ制作を提案する個人事業主です。
営業メールの「件名」と「冒頭のあいさつ文」だけを書きます（本文の残りと署名は別途付けます）。

守ること:
- 押し売りしない、丁寧で短い文章。
- 冒頭では、そのお店の具体的な良さ（口コミで評判の点など）に1つ触れ、実際に調べたことが伝わるようにする。
- 「突然のご連絡失礼いたします」から始める。
- 件名は30字以内。「【ご提案】」などの煽りや絵文字は使わない。お店の名前を入れる。"""


def has_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))


def shop_brief(lead: dict[str, Any]) -> str:
    place = lead.get("place_json") or {}
    if isinstance(place, str):
        place = json.loads(place or "{}")
    reviews = [
        {"rating": rv.get("rating"), "text": (rv.get("text") or {}).get("text", "")}
        for rv in place.get("reviews", [])
    ]
    brief = {
        "店名": lead["name"],
        "業種": lead.get("category") or place.get("primaryType"),
        "住所": lead.get("address"),
        "評価": lead.get("rating"),
        "口コミ件数": lead.get("review_count"),
        "営業時間": (place.get("regularOpeningHours") or {}).get("weekdayDescriptions"),
        "価格帯": place.get("priceLevel"),
        "Googleの概要": (place.get("editorialSummary") or {}).get("text"),
        "お客さんの声（参考・転載禁止）": reviews,
    }
    return json.dumps(brief, ensure_ascii=False, indent=2)


def _call_claude(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        # 安全分類器で断られた場合に別モデルへ自動で切り替える
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude がこのリクエストを処理できませんでした（refusal）")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("出力が途中で切れました（max_tokens）")
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def generate_site_content(lead: dict[str, Any]) -> dict[str, Any]:
    if not has_credentials():
        return _fallback_site_content(lead)
    return _call_claude(
        SITE_SYSTEM,
        "次のお店のホームページ用の文章を作ってください。\n\n" + shop_brief(lead),
        SITE_SCHEMA,
    )


def generate_email_intro(lead: dict[str, Any]) -> dict[str, Any]:
    if not has_credentials():
        return {
            "subject": f"{lead['name']}様 ホームページ制作のご提案",
            "opening": (
                "突然のご連絡失礼いたします。\n"
                f"Googleマップで{lead['name']}様の口コミを拝見し、"
                "多くのお客様に愛されているお店だと感じてご連絡いたしました。"
            ),
        }
    return _call_claude(
        EMAIL_SYSTEM,
        "次のお店宛ての営業メールの件名と冒頭文を書いてください。\n\n" + shop_brief(lead),
        EMAIL_SCHEMA,
    )


CONTENT_INSTRUCTIONS = (
    "各お店について、SITE_SCHEMA に従ったサイト文章と、営業メールの email_subject / email_opening を作り、"
    "{slug: {...}} の形の JSON で output/contents.json に保存してください。"
    "書き方のルールは site_rules / email_rules に従うこと。"
)


def export_briefs(leads: list[dict[str, Any]]) -> dict[str, Any]:
    """APIキー無しで Claude（Claude Code やチャット）に文章を書いてもらうための依頼ファイル。"""
    return {
        "instructions": CONTENT_INSTRUCTIONS,
        "site_rules": SITE_SYSTEM,
        "email_rules": EMAIL_SYSTEM,
        "schema": {**SITE_SCHEMA, "properties": {
            **SITE_SCHEMA["properties"],
            "email_subject": EMAIL_SCHEMA["properties"]["subject"],
            "email_opening": EMAIL_SCHEMA["properties"]["opening"],
        }},
        "shops": [{"slug": l["slug"], "info": json.loads(shop_brief(l))} for l in leads],
    }


REQUIRED_SITE_KEYS = [k for k in SITE_SCHEMA["required"]]


def validate_content(c: dict[str, Any]) -> list[str]:
    missing = [k for k in REQUIRED_SITE_KEYS if k not in c]
    if c.get("theme") not in THEMES:
        c["theme"] = "warm"
    return missing


def _fallback_site_content(lead: dict[str, Any]) -> dict[str, Any]:
    name = lead["name"]
    category = lead.get("category") or "お店"
    return {
        "catchcopy": f"まちの{category}、{name}",
        "lead": f"地域の皆さまに支えられて営業している{category}です。どなたでもお気軽にお立ち寄りください。",
        "about": f"{name}は、{lead.get('address', '')}にある{category}です。\n"
                 "一人ひとりのお客様との時間を大切に、心を込めてお迎えしています。",
        "features": [
            {"title": "居心地のよい空間", "text": "ゆっくり過ごしていただけるよう、くつろげる雰囲気づくりを心がけています。"},
            {"title": "丁寧なおもてなし", "text": "初めての方にも安心してご利用いただけるよう、丁寧な対応を大切にしています。"},
            {"title": "地域に根ざして", "text": "地元の皆さまに長く愛されるお店を目指しています。"},
        ],
        "recommended": [],
        "scenes": ["ふだん使いに", "ご友人と", "ご家族で"],
        "theme": "warm",
        "hero_emoji": "✨",
        "meta_description": f"{name}（{category}）の公式サイト。営業時間・アクセス・お店の紹介。",
    }

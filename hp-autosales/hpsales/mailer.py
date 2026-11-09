"""営業メールの作成と送信。

特定電子メール法への対応:
- 送信者の氏名/名称・住所・連絡先・配信停止の方法を必ずフッターに入れる
- 送信先は「お店がWeb等で公表しているアドレス」に限定し、確認した場所を email_source に記録する
- 配信停止の申し出は suppression テーブルに入れ、以後は二度と送らない
- 送信は人が approve したリードだけ（自動で無差別に送らない）
"""
from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from .config import settings

BODY_TEMPLATE = """{name} ご担当者様

{opening}

ご挨拶代わりに、{name}様のホームページのサンプルを作成いたしました。
スマートフォンでもご覧いただけます。

▼サンプルページ
{preview_url}

※Googleマップ等の公開情報をもとに作成した見本で、検索エンジンには表示されない設定にしております。
　お店の公式サイトとして公開・宣伝しているものではありません。

電話ボタン・地図・営業時間など、お客様がお店を探すときに知りたい情報をひとつのページにまとめました。
写真やメニュー、文章はご希望に合わせて差し替えいたします。

■ 料金
・制作費：{price_initial}
・運用費：{price_monthly}

ご興味がございましたら、このメールにご返信いただくだけで結構です。
ご不要の場合はサンプルページも削除いたしますので、そのままお捨て置きください。

何卒よろしくお願いいたします。
"""

FOOTER_TEMPLATE = """
──────────────────
{company}{sender_name}
住所：{address}
{phone_line}メール：{sender_email}

※今後このようなご案内が不要な場合は、お手数ですが
　{optout_email} 宛に「配信停止」とご返信ください。以後お送りいたしません。
──────────────────
"""


def build_body(lead: dict[str, Any], opening: str) -> str:
    body = BODY_TEMPLATE.format(
        name=lead["name"],
        opening=opening.strip(),
        preview_url=lead.get("preview_url") or "(公開URL未設定)",
        price_initial=settings.price_initial,
        price_monthly=settings.price_monthly,
    )
    return body + footer()


def footer() -> str:
    return FOOTER_TEMPLATE.format(
        company=(settings.sender_company + "　") if settings.sender_company else "",
        sender_name=settings.sender_name,
        address=settings.sender_address,
        phone_line=f"電話：{settings.sender_phone}\n" if settings.sender_phone else "",
        sender_email=settings.sender_email,
        optout_email=settings.optout_email,
    )


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.sender_company or settings.sender_name, settings.sender_email))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.sender_email.split("@")[-1] or None)
    msg["List-Unsubscribe"] = f"<mailto:{settings.optout_email}?subject=%E9%85%8D%E4%BF%A1%E5%81%9C%E6%AD%A2>"
    msg.set_content(body)
    return msg


def save_eml(slug: str, msg: EmailMessage) -> Path:
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    path = settings.outbox_dir / f"{slug}.eml"
    path.write_bytes(bytes(msg))
    return path


class SmtpSender:
    """1回の接続でまとめて送る。送信間隔を空けてスパム判定を避ける。"""

    def __init__(self) -> None:
        if not (settings.smtp_user and settings.smtp_password):
            raise SystemExit("SMTP_USER / SMTP_PASSWORD が未設定です")
        self.server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60)
        self.server.starttls(context=ssl.create_default_context())
        self.server.login(settings.smtp_user, settings.smtp_password)
        self._last = 0.0

    def send(self, msg: EmailMessage) -> None:
        wait = settings.send_interval_sec - (time.monotonic() - self._last)
        if self._last and wait > 0:
            time.sleep(wait)
        self.server.send_message(msg)
        self._last = time.monotonic()

    def close(self) -> None:
        try:
            self.server.quit()
        except smtplib.SMTPException:
            pass

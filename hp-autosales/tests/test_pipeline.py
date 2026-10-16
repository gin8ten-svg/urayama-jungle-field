"""外部API（Google / Netlify / SMTP / Claude）をモックして一連の流れを確認する。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ANTHROPIC_API_KEY", None)

from hpsales import cli, content, db, finder  # noqa: E402
from hpsales.config import settings  # noqa: E402

SAMPLE = json.loads((ROOT / "samples" / "sample_place.json").read_text(encoding="utf-8"))


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def fake_post(url, **kw):
    if "searchText" in url:
        return FakeResp({"places": [
            {**SAMPLE, "websiteUri": "https://www.instagram.com/x"},
            {"id": "own", "displayName": {"text": "HPあり"}, "websiteUri": "https://shop.example.jp",
             "userRatingCount": 50, "businessStatus": "OPERATIONAL"},
            {"id": "few", "displayName": {"text": "口コミ少"}, "userRatingCount": 1, "businessStatus": "OPERATIONAL"},
        ]})
    if url.endswith("/sites"):
        return FakeResp({"id": "site123"})
    if url.endswith("/deploys"):
        return FakeResp({"ssl_url": "https://demo.netlify.app"})
    raise AssertionError(url)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.patches = [
            mock.patch.multiple(settings, db_path=t / "db.sqlite", sites_dir=t / "sites", outbox_dir=t / "outbox",
                                google_maps_api_key="k", netlify_token="n", preview_base_url="",
                                sender_name="山田", sender_address="埼玉県", sender_email="me@example.com",
                                optout_email="me@example.com", smtp_user="u", smtp_password="p",
                                send_interval_sec=0, daily_send_limit=10),
            mock.patch("hpsales.finder.requests.post", side_effect=fake_post),
            mock.patch("hpsales.finder.requests.get", return_value=FakeResp(SAMPLE)),
            mock.patch("hpsales.deploy.requests.post", side_effect=fake_post),
            mock.patch("hpsales.content._has_credentials", return_value=False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_classify(self):
        self.assertEqual(finder.classify_website(None), "none")
        self.assertEqual(finder.classify_website("https://www.instagram.com/a"), "sns_only")
        self.assertEqual(finder.classify_website("https://tabelog.com/saitama/A1"), "portal_only")
        self.assertEqual(finder.classify_website("https://myshop.jp"), "own")

    def test_full_flow(self):
        cli.main(["run", "浦和 ラーメン"])
        conn = db.connect()
        rows = db.all_leads(conn)
        self.assertEqual(len(rows), 1)  # HPあり・口コミ少は除外
        row = rows[0]
        self.assertEqual(row["status"], "deployed")  # メール未登録なので下書きされない
        self.assertEqual(row["preview_url"], "https://demo.netlify.app")
        html = Path(row["site_path"]).read_text(encoding="utf-8")
        self.assertIn("noindex", html)
        self.assertIn("提案用サンプル", html)
        self.assertNotIn("日本、", html)

        with self.assertRaises(SystemExit):  # 公表元の記録なしは拒否
            cli.main(["set-email", row["slug"], "shop@example.com"])
        cli.main(["set-email", row["slug"], "shop@example.com", "--source", "https://instagram.com/x"])
        cli.main(["draft", "--all"])
        row = db.get(conn, row["place_id"])
        self.assertEqual(row["status"], "drafted")
        self.assertIn("配信停止", row["email_body"])
        self.assertIn("埼玉県", row["email_body"])

        with mock.patch("hpsales.mailer.smtplib.SMTP") as smtp:
            cli.main(["send", "--live"])  # 未承認なので送られない
            smtp.return_value.send_message.assert_not_called()
            cli.main(["approve", row["slug"]])
            cli.main(["send"])  # ドライラン
            smtp.return_value.send_message.assert_not_called()
            cli.main(["send", "--live"])
            smtp.return_value.send_message.assert_called_once()
            msg = smtp.return_value.send_message.call_args[0][0]
            self.assertEqual(msg["To"], "shop@example.com")
            self.assertIn("mailto:", msg["List-Unsubscribe"])
        self.assertEqual(db.get(conn, row["place_id"])["status"], "sent")

    def test_optout_blocks_send(self):
        cli.main(["search", "浦和 ラーメン"])
        conn = db.connect()
        row = db.all_leads(conn)[0]
        db.update(conn, row["place_id"], email="shop@example.com", email_source="x", status="approved",
                  email_subject="s", email_body="b")
        cli.main(["optout", "SHOP@example.com"])
        with mock.patch("hpsales.mailer.smtplib.SMTP") as smtp:
            cli.main(["send", "--live"])
            smtp.return_value.send_message.assert_not_called()
        self.assertEqual(db.get(conn, row["place_id"])["status"], "opted_out")


if __name__ == "__main__":
    unittest.main()

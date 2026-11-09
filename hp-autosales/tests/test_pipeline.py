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

from hpsales import cli, content, db, discover, finder  # noqa: E402
from hpsales.config import settings  # noqa: E402

SAMPLE = json.loads((ROOT / "samples" / "sample_place.json").read_text(encoding="utf-8"))


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
        self.ok = status < 400

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


def fake_claude(system, user, schema):
    if schema is content.EMAIL_SCHEMA:
        return {"subject": "件名", "opening": "突然のご連絡失礼いたします。"}
    return content._fallback_site_content({"name": "テスト", "category": "ラーメン屋"})


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
            mock.patch("hpsales.content.has_credentials", return_value=True),
            mock.patch("hpsales.content._call_claude", side_effect=fake_claude),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_api_error_shows_reason(self):
        err = FakeResp({"error": {"message": "This API method requires billing to be enabled."}}, 403)
        with mock.patch("hpsales.finder.requests.post", return_value=err):
            with self.assertRaisesRegex(RuntimeError, "請求先アカウント"):
                finder.search("x")

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


class DiscoverTest(unittest.TestCase):
    T = {**discover.DEFAULTS, "areas": ["A区", "B区"], "categories": ["ラーメン", "カフェ"],
         "max_queries_per_run": 3, "requery_days": 30}

    def test_plan_prefers_unsearched_then_oldest(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        history = {
            "A区 ラーメン": "2026-10-10T00:00:00+00:00",  # 最近 → 対象外
            "A区 カフェ": "2026-08-01T00:00:00+00:00",    # 古い → 再検索
            "B区 ラーメン": "2026-07-01T00:00:00+00:00",  # もっと古い
        }
        self.assertEqual(discover.plan(self.T, history, now=now),
                         ["B区 カフェ", "B区 ラーメン", "A区 カフェ"])
        self.assertEqual(discover.plan(self.T, history, limit=1, now=now), ["B区 カフェ"])


class AutoTest(PipelineTest):
    def test_auto_runs_queries_and_skips_known(self):
        cfg = Path(self.tmp.name) / "targets.json"
        cfg.write_text(json.dumps({"areas": ["浦和区"], "categories": ["ラーメン", "カフェ"]}), encoding="utf-8")
        with mock.patch("hpsales.finder.requests.get", return_value=FakeResp(SAMPLE)) as get:
            cli.main(["auto", "--targets", str(cfg)])
            conn = db.connect()
            self.assertEqual(set(db.search_history(conn)), {"浦和区 ラーメン", "浦和区 カフェ"})
            self.assertEqual(len(db.all_leads(conn)), 1)
            self.assertEqual(get.call_count, 1)  # 2回目の検索では既知の店の詳細取得を省く
            self.assertEqual(db.all_leads(conn)[0]["status"], "deployed")
            cli.main(["auto", "--targets", str(cfg)])  # 全部実施済み → 何も検索しない
            self.assertEqual(get.call_count, 1)

    test_full_flow = None
    test_optout_blocks_send = None
    test_classify = None

    def test_manual_content_mode_without_api_key(self):
        """APIキー無し: briefs.json を書き出し → 文章を取り込むとサイト化・公開される"""
        with mock.patch("hpsales.content.has_credentials", return_value=False):
            cli.main(["run", "浦和 ラーメン"])
            conn = db.connect()
            row = db.all_leads(conn)[0]
            self.assertEqual(row["status"], "new")
            briefs = json.loads(settings.briefs_path.read_text(encoding="utf-8"))
            self.assertEqual(briefs["shops"][0]["slug"], row["slug"])
            self.assertIn("email_opening", briefs["schema"]["properties"])

            c = {**content._fallback_site_content(dict(row)), "theme": "unknown",
                 "email_subject": "AIが書いた件名", "email_opening": "AIが書いた冒頭"}
            f = Path(self.tmp.name) / "contents.json"
            f.write_text(json.dumps({row["slug"]: c}, ensure_ascii=False), encoding="utf-8")
            cli.main(["import-content", str(f)])
            row = db.get(conn, row["slug"])
            self.assertEqual(row["status"], "deployed")
            self.assertEqual(json.loads(row["content_json"])["theme"], "warm")

            cli.main(["set-email", row["slug"], "shop@example.com", "--source", "instagram"])
            cli.main(["draft", "--all"])
            row = db.get(conn, row["slug"])
            self.assertEqual(row["email_subject"], "AIが書いた件名")
            self.assertIn("AIが書いた冒頭", row["email_body"])

    def test_quota_stops_api_calls(self):
        cfg = Path(self.tmp.name) / "targets.json"
        cfg.write_text(json.dumps({"areas": ["浦和区"], "categories": ["ラーメン", "カフェ"]}), encoding="utf-8")
        with mock.patch.multiple(settings, places_search_monthly_limit=1):
            cli.main(["auto", "--targets", str(cfg), "--search-only"])
            conn = db.connect()
            self.assertEqual(db.usage(conn, "search"), 1)
            self.assertEqual(list(db.search_history(conn)), ["浦和区 ラーメン"])  # 2件目は上限で止まる
        used = db.usage(conn, "details")
        with mock.patch.multiple(settings, places_details_monthly_limit=used, places_search_monthly_limit=99):
            with mock.patch.object(db, "known_place_ids", return_value=set()), \
                 mock.patch("hpsales.finder.requests.get") as get:
                cli.main(["search", "別の検索"])  # 詳細取得が必要だが上限なので呼ばない
                get.assert_not_called()
            self.assertEqual(db.usage(conn, "details"), used)


if __name__ == "__main__":
    unittest.main()

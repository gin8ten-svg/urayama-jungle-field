"""コマンドライン入口。 `python -m hpsales <command>` で実行。"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from . import content, db, deploy, discover, finder, mailer, render
from .config import ROOT, settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CSV_FIELDS = ["place_id", "name", "category", "address", "phone", "rating", "review_count",
              "web_status", "website", "maps_url", "preview_url", "status", "email", "email_source", "note"]


def _targets(conn, keys: list[str], all_flag: bool, statuses: list[str]):
    if all_flag:
        return db.by_status(conn, statuses)
    rows = []
    for k in keys:
        row = db.get(conn, k)
        if row is None:
            print(f"見つかりません: {k}")
        else:
            rows.append(row)
    return rows


# ---------------------------------------------------------------- search
def quota_guard(conn):
    """Google の無料枠を超えないよう、API を呼ぶ前に今月の回数を数える。"""
    limits = {"search": settings.places_search_monthly_limit, "details": settings.places_details_monthly_limit}

    def guard(sku: str) -> None:
        if not db.consume(conn, sku, limits[sku]):
            raise finder.QuotaExceeded(
                f"今月の無料枠の上限に達したので止めました（{sku}: {limits[sku]}回）。来月また動きます")
    return guard


def search_and_store(conn, query: str, max_results: int, min_reviews: int, include_sns: bool) -> int:
    try:
        leads = finder.find_leads(query, max_results, min_reviews, include_sns=include_sns,
                                  skip_ids=db.known_place_ids(conn), guard=quota_guard(conn))
    except finder.QuotaExceeded as e:
        for lead in getattr(e, "partial", []):  # 上限までに見つかった分は保存（検索語は未完了扱い）
            lead["slug"] = finder.slugify(lead["name"], lead["place_id"])
            db.upsert_lead(conn, lead)
        raise
    for lead in leads:
        lead["slug"] = finder.slugify(lead["name"], lead["place_id"])
        db.upsert_lead(conn, lead)
    db.log_search(conn, query, len(leads))
    print(f"「{query}」: 新しいHPなし候補 {len(leads)} 件")
    for lead in leads:
        print(f"  [{lead['web_status']:<11}] {lead['name']}  ★{lead['rating']} ({lead['review_count']}件)  {lead['slug']}")
    return len(leads)


def cmd_search(args) -> None:
    try:
        search_and_store(db.connect(), args.query, args.max, args.min_reviews, not args.no_sns)
    except finder.QuotaExceeded as e:
        print(e)


def cmd_list(args) -> None:
    conn = db.connect()
    rows = db.by_status(conn, [args.status]) if args.status else db.all_leads(conn)
    for r in rows:
        mail = r["email"] or "-"
        print(f"{r['status']:<9} {r['slug']:<38} {r['name'][:20]:<20} ★{r['rating'] or '-'} {mail}")
    print(f"合計 {len(rows)} 件")


# ---------------------------------------------------------------- email address 管理
def cmd_export(args) -> None:
    conn = db.connect()
    with open(args.file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in db.all_leads(conn):
            w.writerow({k: r[k] for k in CSV_FIELDS})
    print(f"{args.file} に書き出しました。email と email_source（確認したURL）を埋めて import してください。")


def cmd_import(args) -> None:
    conn = db.connect()
    n = 0
    with open(args.file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not db.get(conn, row["place_id"]):
                continue
            fields = {}
            email = (row.get("email") or "").strip()
            if email:
                if not EMAIL_RE.match(email):
                    print(f"  不正なアドレスのためスキップ: {row['name']} {email}")
                    continue
                if not (row.get("email_source") or "").strip():
                    print(f"  email_source が空のためスキップ: {row['name']}（公表元の記録が必要です）")
                    continue
                fields.update(email=email, email_source=row["email_source"].strip())
            if (row.get("status") or "").strip() == "skipped":
                fields["status"] = "skipped"
            if row.get("note"):
                fields["note"] = row["note"]
            if fields:
                db.update(conn, row["place_id"], **fields)
                n += 1
    print(f"{n} 件更新しました")


def cmd_set_email(args) -> None:
    conn = db.connect()
    row = db.get(conn, args.key)
    if not row:
        sys.exit(f"見つかりません: {args.key}")
    if not EMAIL_RE.match(args.email):
        sys.exit("メールアドレスの形式が不正です")
    db.update(conn, row["place_id"], email=args.email, email_source=args.source)
    print(f"{row['name']}: {args.email}（公表元: {args.source}）")


# ---------------------------------------------------------------- build / deploy
class NeedsContent(Exception):
    pass


def build_one(conn, row, preview: bool = True, template: bool = False) -> Path:
    """文章は ①取り込み済み(content_json) ②Claude API ③テンプレ(template=True) の順で使う。"""
    lead = dict(row)
    print(f"サイト生成中: {lead['name']} ...", flush=True)
    if lead.get("content_json"):
        c = json.loads(lead["content_json"])
    elif content.has_credentials():
        c = content.generate_site_content(lead)
        db.update(conn, lead["place_id"], content_json=json.dumps(c, ensure_ascii=False))
    elif template:
        c = content._fallback_site_content(lead)
    else:
        raise NeedsContent("文章がまだありません（`briefs` → Claude に書いてもらう → `import-content`）")
    path = render.write_site(lead["slug"], render.render_site(lead, c, preview=preview), c)
    db.update(conn, lead["place_id"], site_path=str(path),
              status="built" if lead["status"] == "new" else lead["status"])
    print(f"  → {path}")
    return path


def cmd_build(args) -> None:
    conn = db.connect()
    for row in _targets(conn, args.keys, args.all, ["new"]):
        try:
            build_one(conn, row, template=args.template)
        except Exception as e:  # 1件の失敗で全体を止めない
            print(f"  失敗: {row['name']}: {e}")


def deploy_one(conn, row) -> str:
    site_dir = Path(row["site_path"]).parent
    url, site_id = deploy.publish(row["slug"], site_dir, row["netlify_site_id"])
    fields = {"preview_url": url}
    if site_id:
        fields["netlify_site_id"] = site_id
    if row["status"] == "built":
        fields["status"] = "deployed"
    db.update(conn, row["place_id"], **fields)
    print(f"公開: {row['name']} → {url}")
    return url


def cmd_deploy(args) -> None:
    conn = db.connect()
    for row in _targets(conn, args.keys, args.all, ["built"]):
        if not row["site_path"]:
            print(f"未生成: {row['name']}（先に build してください）")
            continue
        try:
            deploy_one(conn, row)
        except Exception as e:
            print(f"  失敗: {row['name']}: {e}")


# ---------------------------------------------------------------- draft / approve / send
def draft_one(conn, row) -> None:
    lead = dict(row)
    saved = json.loads(lead.get("content_json") or "{}")
    if saved.get("email_subject") and saved.get("email_opening"):
        intro = {"subject": saved["email_subject"], "opening": saved["email_opening"]}
    else:
        intro = content.generate_email_intro(lead)
    body = mailer.build_body(lead, intro["opening"])
    db.update(conn, lead["place_id"], email_subject=intro["subject"], email_body=body, status="drafted")
    eml = mailer.save_eml(lead["slug"], mailer.build_message(lead["email"], intro["subject"], body))
    print(f"下書き: {lead['name']} <{lead['email']}> → {eml}")


def cmd_draft(args) -> None:
    missing = settings.missing_sender_fields()
    if missing:
        sys.exit(f"送信者情報が未設定です（特定電子メール法で表示が必要）: {', '.join(missing)}")
    conn = db.connect()
    for row in _targets(conn, args.keys, args.all, ["deployed"]):
        if not row["email"]:
            print(f"メール未登録のためスキップ: {row['name']}（電話・SNS等で営業してください）")
            continue
        if not row["preview_url"]:
            print(f"未公開のためスキップ: {row['name']}")
            continue
        if db.is_suppressed(conn, row["email"]):
            print(f"配信停止済み: {row['name']}")
            continue
        try:
            draft_one(conn, row)
        except Exception as e:
            print(f"  失敗: {row['name']}: {e}")


def cmd_review(args) -> None:
    conn = db.connect()
    rows = db.by_status(conn, ["drafted"])
    for r in rows:
        print("=" * 70)
        print(f"[{r['slug']}] To: {r['email']}（公表元: {r['email_source']}）")
        print(f"件名: {r['email_subject']}\n")
        print(r["email_body"])
    print(f"\n下書き {len(rows)} 件。問題なければ `approve <slug>` か `approve --all`")


def cmd_approve(args) -> None:
    conn = db.connect()
    for row in _targets(conn, args.keys, args.all, ["drafted"]):
        if row["status"] != "drafted":
            print(f"下書き状態ではありません: {row['name']} ({row['status']})")
            continue
        db.update(conn, row["place_id"], status="approved")
        print(f"承認: {row['name']}")


def cmd_skip(args) -> None:
    conn = db.connect()
    for k in args.keys:
        row = db.get(conn, k)
        if row:
            db.update(conn, row["place_id"], status="skipped", note=args.reason or row["note"])
            print(f"対象外にしました: {row['name']}")


def cmd_send(args) -> None:
    missing = settings.missing_sender_fields()
    if missing:
        sys.exit(f"送信者情報が未設定です: {', '.join(missing)}")
    conn = db.connect()
    rows = db.by_status(conn, ["approved"])
    remaining = settings.daily_send_limit - db.sent_today(conn)
    if remaining <= 0:
        sys.exit(f"本日の送信上限（{settings.daily_send_limit}通）に達しています")
    rows = rows[:remaining]
    if not rows:
        print("承認済みのメールはありません")
        return
    sender = mailer.SmtpSender() if args.live else None
    try:
        for row in rows:
            if db.is_suppressed(conn, row["email"]):
                db.update(conn, row["place_id"], status="opted_out")
                continue
            msg = mailer.build_message(row["email"], row["email_subject"], row["email_body"])
            if sender:
                sender.send(msg)
                db.update(conn, row["place_id"], status="sent", sent_at=db.now())
                print(f"送信: {row['name']} <{row['email']}>")
            else:
                print(f"[DRY RUN] {row['name']} <{row['email']}> 件名: {row['email_subject']}")
    finally:
        if sender:
            sender.close()
    if not args.live:
        print("\n※ドライランです。実際に送るには `send --live`")


def cmd_optout(args) -> None:
    conn = db.connect()
    db.suppress(conn, args.email, args.reason)
    print(f"配信停止リストに追加: {args.email}")


# ---------------------------------------------------------------- まとめて実行
def cmd_run(args) -> None:
    """検索 → サイト生成 → 公開 → 下書き作成 まで。送信は人の承認後。"""
    cmd_search(args)
    process_pipeline(db.connect())


def cmd_auto(args) -> None:
    """targets.json のエリア × 業種を順番に自動検索し、続けてサイト生成〜下書きまで行う。"""
    targets = discover.load_targets(Path(args.targets) if args.targets else None)
    conn = db.connect()
    queries = discover.plan(targets, db.search_history(conn), args.max_queries)
    total = len(discover.all_queries(targets))
    done = len(set(db.search_history(conn)) & set(discover.all_queries(targets)))
    print(f"検索語 {total} 通り中 {done} 通り実施済み。今回 {len(queries)} 件を検索します")
    if args.plan:
        for q in queries:
            print(f"  {q}")
        return
    found = 0
    for q in queries:
        try:
            found += search_and_store(conn, q, targets["max_results"], targets["min_reviews"],
                                      targets["include_sns"])
        except finder.QuotaExceeded as e:
            print(e)
            break
        except Exception as e:
            print(f"  検索失敗: {q}: {e}")
    print(f"\n新しい候補 合計 {found} 件")
    if not queries:
        print("すべての検索語が最近実施済みです（requery_days を過ぎると再検索します）")
    if not args.search_only:
        process_pipeline(conn)


def process_pipeline(conn) -> None:
    waiting = []
    for row in db.by_status(conn, ["new"]):
        try:
            build_one(conn, row)
        except NeedsContent:
            waiting.append(row)
        except Exception as e:
            print(f"  失敗: {row['name']}: {e}")
    if waiting:
        write_briefs(waiting)
    if settings.netlify_token or settings.preview_base_url:
        for row in db.by_status(conn, ["built"]):
            try:
                deploy_one(conn, row)
            except Exception as e:
                print(f"  公開失敗: {row['name']}: {e}")
    if not settings.missing_sender_fields():
        cmd_draft(argparse.Namespace(keys=[], all=True))
    print("\n次の手順: `review` で下書きを確認 → `approve` → `send --live`")
    print("メールアドレス未登録のお店は `export` → CSVに記入 → `import` → `draft --all`")


# ---------------------------------------------------------------- APIキー無しで文章を作る
def write_briefs(rows) -> None:
    data = content.export_briefs([dict(r) for r in rows])
    settings.briefs_path.parent.mkdir(parents=True, exist_ok=True)
    settings.briefs_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n文章待ちのお店 {len(rows)} 件 → {settings.briefs_path}")
    print("Claude Code に「briefs.json の文章を書いて」と頼み、できたら `import-content output/contents.json`")


def cmd_briefs(args) -> None:
    conn = db.connect()
    rows = [r for r in db.by_status(conn, ["new"]) if not r["content_json"]]
    if not rows:
        print("文章待ちのお店はありません")
        return
    write_briefs(rows)


def cmd_import_content(args) -> None:
    conn = db.connect()
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    for slug, c in data.items():
        row = db.get(conn, slug)
        if not row:
            print(f"見つかりません: {slug}")
            continue
        missing = content.validate_content(c)
        if missing:
            print(f"項目不足のためスキップ: {slug}: {', '.join(missing)}")
            continue
        db.update(conn, row["place_id"], content_json=json.dumps(c, ensure_ascii=False))
        build_one(conn, db.get(conn, slug))
    if settings.netlify_token or settings.preview_base_url:
        for row in db.by_status(conn, ["built"]):
            try:
                deploy_one(conn, row)
            except Exception as e:
                print(f"  公開失敗: {row['name']}: {e}")


def cmd_usage(args) -> None:
    conn = db.connect()
    print(f"今月の Google API 利用回数（無料枠は各1,000回）")
    print(f"  検索: {db.usage(conn, 'search')} / 上限 {settings.places_search_monthly_limit}")
    print(f"  詳細: {db.usage(conn, 'details')} / 上限 {settings.places_details_monthly_limit}")


def cmd_demo(args) -> None:
    """API キー無しで動作確認。サンプル店舗データからサイトとメール文面を作る。"""
    sample = json.loads((ROOT / "samples" / "sample_place.json").read_text(encoding="utf-8"))
    lead = finder.to_lead(sample, "demo")
    lead["slug"] = finder.slugify(lead["name"], lead["place_id"])
    c = content.generate_site_content(lead)
    path = render.write_site(lead["slug"], render.render_site(lead, c, preview=True), c)
    print(f"サイト: {path}")
    lead["preview_url"] = f"https://example.netlify.app/{lead['slug']}/"
    intro = content.generate_email_intro(lead)
    body = mailer.build_body(lead, intro["opening"])
    eml = mailer.save_eml(lead["slug"], mailer.build_message("shop@example.com", intro["subject"], body))
    print(f"メール: {eml}\n\n件名: {intro['subject']}\n\n{body}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="hpsales", description="HP未所持店舗へのサイト提案を自動化")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_search_args(sp):
        sp.add_argument("query", help='例: "浦和 ラーメン" "さいたま市 美容室"')
        sp.add_argument("--max", type=int, default=60, help="検索件数（最大60）")
        sp.add_argument("--min-reviews", type=int, default=5, help="口コミがこれ未満の店は除外")
        sp.add_argument("--no-sns", action="store_true", help="SNS/グルメサイトのみの店を除外")

    sp = sub.add_parser("search", help="HPなしの店舗を検索して登録"); add_search_args(sp); sp.set_defaults(f=cmd_search)
    sp = sub.add_parser("run", help="検索→生成→公開→下書き まで一括"); add_search_args(sp); sp.set_defaults(f=cmd_run)

    sp = sub.add_parser("auto", help="targets.json のエリア×業種を自動で検索→生成→公開→下書き")
    sp.add_argument("--targets", help="設定ファイル（既定: targets.json）")
    sp.add_argument("--max-queries", type=int, help="今回検索する件数（既定: max_queries_per_run）")
    sp.add_argument("--search-only", action="store_true", help="検索だけ行う")
    sp.add_argument("--plan", action="store_true", help="検索予定を表示するだけ（API を使わない）")
    sp.set_defaults(f=cmd_auto)

    sp = sub.add_parser("list", help="リード一覧"); sp.add_argument("--status"); sp.set_defaults(f=cmd_list)
    sp = sub.add_parser("export", help="CSV に書き出し"); sp.add_argument("file"); sp.set_defaults(f=cmd_export)
    sp = sub.add_parser("import", help="CSV からメールアドレス等を取り込み"); sp.add_argument("file"); sp.set_defaults(f=cmd_import)
    sp = sub.add_parser("set-email", help="メールアドレスを1件登録")
    sp.add_argument("key", help="slug か place_id"); sp.add_argument("email")
    sp.add_argument("--source", required=True, help="アドレスが公表されていた場所（URL等）")
    sp.set_defaults(f=cmd_set_email)

    for name, fn, help_ in [("build", cmd_build, "サイト生成（new を対象）"),
                            ("deploy", cmd_deploy, "サイト公開（built を対象）"),
                            ("draft", cmd_draft, "営業メール下書き（deployed を対象）"),
                            ("approve", cmd_approve, "下書きを承認（drafted を対象）")]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("keys", nargs="*"); sp.add_argument("--all", action="store_true")
        if name == "build":
            sp.add_argument("--template", action="store_true", help="文章が無い店はテンプレ文章で作る")
        sp.set_defaults(f=fn)

    sp = sub.add_parser("briefs", help="文章待ちのお店の依頼ファイルを作る（APIキー無しモード）")
    sp.set_defaults(f=cmd_briefs)
    sp = sub.add_parser("import-content", help="Claude が書いた文章を取り込んでサイト化")
    sp.add_argument("file"); sp.set_defaults(f=cmd_import_content)
    sp = sub.add_parser("usage", help="今月の Google API 利用回数"); sp.set_defaults(f=cmd_usage)
    sp = sub.add_parser("review", help="下書きメールを確認"); sp.set_defaults(f=cmd_review)
    sp = sub.add_parser("skip", help="対象外にする"); sp.add_argument("keys", nargs="+"); sp.add_argument("--reason")
    sp.set_defaults(f=cmd_skip)
    sp = sub.add_parser("send", help="承認済みメールを送信（デフォルトはドライラン）")
    sp.add_argument("--live", action="store_true", help="実際に送信する"); sp.set_defaults(f=cmd_send)
    sp = sub.add_parser("optout", help="配信停止を登録"); sp.add_argument("email")
    sp.add_argument("--reason", default="opt-out"); sp.set_defaults(f=cmd_optout)
    sp = sub.add_parser("demo", help="APIキー無しで動作確認"); sp.set_defaults(f=cmd_demo)

    args = p.parse_args(argv)
    args.f(args)

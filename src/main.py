from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mailer import plain_text, send_email
from matching import dedupe, remember, rejection_stats, score_job, select
from render import build_items, render_html, render_pdf
from sources import enrich_with_details, fetch_all
from utils import ensure_dir, env, load_config, local_date

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("weasyprint").setLevel(logging.WARNING)
logging.getLogger("fontTools").setLevel(logging.WARNING)
log = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily bilingual job brief")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--out", default=str(ROOT / "out"))
    p.add_argument("--dry-run", action="store_true", help="Generate files but do not send email")
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--gulf-only", action="store_true", help="Skip remote sources (faster test)")
    p.add_argument("--remote-only", action="store_true", help="Skip LinkedIn Gulf search")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg["settings"]
    if args.max_items:
        settings["max_items"] = args.max_items
    elif env("MAX_ITEMS"):
        try:
            settings["max_items"] = int(env("MAX_ITEMS"))
        except ValueError:
            pass
    if args.gulf_only:
        for name in ("remotive", "remoteok", "himalayas", "jobicy", "arbeitnow", "weworkremotely"):
            cfg["sources"].setdefault(name, {})["enabled"] = False
        cfg["sources"].setdefault("linkedin", {})["remote"] = False
        settings["max_remote"] = 0
    if args.remote_only:
        cfg["sources"].setdefault("linkedin", {})["gulf"] = False
        settings["max_gulf"] = 0

    tz = settings.get("timezone", "Asia/Dubai")
    date_str = local_date(tz)
    out_dir = ensure_dir(Path(args.out) / date_str)

    log.info("Fetching job sources…")
    raw, failed = fetch_all(cfg)
    log.info("Raw jobs: %d | failed sources: %s", len(raw), failed or "none")

    scored = [score_job(j, cfg) for j in raw]
    stats = rejection_stats(scored)
    log.info("Rejections: %s", stats)
    unique = dedupe(scored)

    # Pull the full description + verified seniority for the best candidates,
    # then re-score with that richer text before the final cut.
    pool = sorted([j for j in unique if j.score > 0], key=lambda x: x.score, reverse=True)
    enrich_with_details(pool, cfg, limit=int(settings.get("detail_limit", 26)))
    scored = [score_job(j, cfg) for j in unique]
    late = rejection_stats(scored)
    if late:
        log.info("Extra rejections after reading full descriptions: %s", late)
        for k, v in late.items():
            stats[k] = stats.get(k, 0) + v

    selected = select(scored, cfg)
    log.info("Selected: %d (gulf=%d remote=%d)", len(selected),
             sum(1 for j in selected if j.region == "gulf"),
             sum(1 for j in selected if j.region == "remote"))

    if not args.dry_run:
        total_seen = remember(cfg, selected)
        log.info("Seen-history now holds %d job IDs", total_seen)

    items = build_items(selected, cfg)
    html = render_html(items, cfg, ROOT / "templates", failed_sources=failed, stats=stats)
    html_path = out_dir / f"jobs-{date_str}.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("HTML saved: %s", html_path)

    pdf_path = out_dir / f"jobs-{date_str}.pdf"
    pdf_ok = render_pdf(html, pdf_path)
    if pdf_ok:
        log.info("PDF saved: %s", pdf_path)

    json_path = out_dir / f"jobs-{date_str}.json"
    json_path.write_text(json.dumps({
        "date": date_str,
        "count": len(items),
        "failed_sources": failed,
        "rejections": stats,
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        log.info("Dry run: email not sent.")
        return 0

    subject = f"{cfg['email']['subject_prefix']} — {date_str} — {len(items)} فرصة"
    attachments = [html_path]
    if pdf_ok:
        attachments.append(pdf_path)
    try:
        from premailer import transform
        email_html = transform(html, keep_style_tags=False, strip_important=False)
    except Exception:
        email_html = html
    try:
        send_email(
            subject=subject, html=email_html,
            plain=plain_text(items, date_str),
            attachments=attachments,
            from_name=cfg["email"].get("from_name", "Daily Job Brief"),
        )
    except Exception as e:
        log.error("Email failed: %s", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line entry point: python -m pgrack {run,capture,parse-file}."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .fetch import SOURCE_URL, FetchBlocked, FetchFailed, fetch
from .parse import ParseError, parse_daily, utc_now_iso
from .store import StoreError, upsert

DEFAULT_CSV = Path("data/pg_rack_rates.csv")
DEFAULT_LOG = Path("data/run_log.csv")


def _gh_output(**kv: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            v = str(v).replace("\r", " ").replace("\n", " ")
            f.write(f"{k}={v}\n")


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def _fail(category: str, message: str) -> int:
    _gh_output(outcome="failed", category=category, details=message)
    _summary(f"**FAILED ({category})**: {message}\n\nDataset not modified.")
    return 1


def _methods(arg: str | None) -> list[str] | None:
    if not arg:
        return None
    methods = [m.strip() for m in arg.split(",") if m.strip()]
    bad = [m for m in methods if m not in ("direct", "browser", "service")]
    if bad:
        raise SystemExit(f"unknown fetch method(s): {', '.join(bad)}")
    return methods


def cmd_run(args: argparse.Namespace) -> int:
    methods = _methods(args.methods)
    try:
        result, failures = fetch(SOURCE_URL, methods)
    except FetchBlocked as e:
        return _fail("blocked", str(e))
    except FetchFailed as e:
        return _fail("fetch", str(e))
    for f in failures:
        print(f"note: {f}")
    retrieved_at = utc_now_iso()
    try:
        record = parse_daily(result.html)
    except ParseError as e:
        if args.save_html:
            Path(args.save_html).write_text(result.html, encoding="utf-8")
        return _fail("parse", str(e))
    return _apply(record, retrieved_at, Path(args.csv), Path(args.log), result.method)


def _apply(record, retrieved_at: str, csv_path: Path, log_path: Path, method: str) -> int:
    try:
        outcome, details = upsert(csv_path, log_path, record, retrieved_at, SOURCE_URL)
    except StoreError as e:
        return _fail("store", str(e))
    date = record.effective_date.isoformat()
    _gh_output(outcome=outcome, effective_date=date, details=details, method=method)
    _summary(
        f"**{outcome}** effective_date={date} via {method}\n\n"
        + "\n".join(f"- {k}: {v or '(blank)'}" for k, v in record.as_csv_fields().items())
        + f"\n\n{details}"
    )
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    methods = _methods(args.methods)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        result, failures = fetch(SOURCE_URL, methods)
        html, status, method = result.html, result.status, result.method
    except FetchFailed as e:
        failures = []
        html, status, method = e.body, e.status, f"FAILED {e}"
    for f in failures:
        print(f"attempt failed: {f}")
        if f.body:
            (out / f"{f.method.replace(':', '_')}.html").write_text(f.body, encoding="utf-8")
    (out / "page.html").write_text(html or "", encoding="utf-8")
    print(f"method={method} status={status} bytes={len(html or '')}")
    # Print the region around the Daily table so it can be read from job logs.
    text = html or ""
    for needle in ("Daily", "Prince George"):
        for m in list(re.finditer(re.escape(needle), text))[:3]:
            lo, hi = max(0, m.start() - 1500), min(len(text), m.end() + 1500)
            print(f"----- context around {needle!r} @ {m.start()} -----")
            print(text[lo:hi])
    try:
        rec = parse_daily(text)
        print("PARSE OK:", json.dumps(rec.as_csv_fields()))
    except ParseError as e:
        print("PARSE FAILED:", e)
    return 0


def cmd_parse_file(args: argparse.Namespace) -> int:
    html = Path(args.html).read_text(encoding="utf-8")
    try:
        record = parse_daily(html)
    except ParseError as e:
        return _fail("parse", str(e))
    print(json.dumps(record.as_csv_fields()))
    if not args.upsert_into:
        return 0
    # Dry-run upsert against a scratch copy; the real dataset is never touched.
    work = Path(tempfile.mkdtemp())
    csv_path, log_path = work / "pg_rack_rates.csv", work / "run_log.csv"
    shutil.copy(args.upsert_into, csv_path)
    rc = _apply(record, utc_now_iso(), csv_path, log_path, "file")
    print(csv_path.read_text(encoding="utf-8"))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pgrack")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, parse, and update the dataset")
    r.add_argument("--csv", default=str(DEFAULT_CSV))
    r.add_argument("--log", default=str(DEFAULT_LOG))
    r.add_argument("--methods", help="comma list of direct,browser,service")
    r.add_argument("--save-html", help="write fetched HTML here if parsing fails")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("capture", help="fetch and save raw HTML for diagnosis")
    c.add_argument("--out", default="capture")
    c.add_argument("--methods")
    c.set_defaults(func=cmd_capture)

    p = sub.add_parser("parse-file", help="parse a saved HTML file")
    p.add_argument("html")
    p.add_argument("--upsert-into", help="dry-run upsert into a scratch copy of this CSV")
    p.set_defaults(func=cmd_parse_file)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from bqtop import __version__
from bqtop.config import Config, ConfigError, load

HINTS = (
    (
        "jobs.listAll",
        "grant roles/bigquery.resourceViewer at that level (project, folder or org), "
        'or use scope = "user" / kind = "audit_log"',
    ),
    ("bigquery.jobs.create", "grant roles/bigquery.jobUser on [source].billing_project"),
    ("Not found: Table", "check [source].table (audit_log) or [source].projects / regions"),
    (
        "could not automatically determine credentials",
        "run `gcloud auth application-default login` or set GOOGLE_APPLICATION_CREDENTIALS",
    ),
    ("Reauthentication", "run `gcloud auth application-default login` again"),
    ("has not been used in project", "enable the BigQuery API on [source].billing_project"),
)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("\nbqtop: aborted", file=sys.stderr)
        return 130
    except Exception as e:  # one readable line instead of a stack trace; --debug or BQTOP_DEBUG=1 for the trace
        if "--debug" in (argv or sys.argv) or os.environ.get("BQTOP_DEBUG"):
            raise
        print(f"bqtop: {' '.join(str(e).split())[:400]}", file=sys.stderr)
        print("bqtop: run with --debug for the full traceback", file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bqtop", description="htop for BigQuery: jobs, principals, projects, cost.")
    ap.add_argument("-c", "--config", help="config file (default: ./bqtop.toml, then ~/.config/bqtop/config.toml)")
    ap.add_argument("-w", "--window", type=float, help="window in hours (overrides config)")
    ap.add_argument(
        "-f", "--filter", default="", help="only jobs matching this text (principal, project, table, query)"
    )
    ap.add_argument("--sort", default="cost", choices=("cost", "bytes", "jobs", "errors", "slots"))
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--json", action="store_true", help="with --once: emit the snapshot as JSON")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="plain-text mode: reprint every N seconds")
    ap.add_argument("--demo", action="store_true", help="run on synthetic data, no GCP needed")
    ap.add_argument("--check", action="store_true", help="check credentials, permissions and the source, then exit")
    ap.add_argument("--init", action="store_true", help="interactive setup, writes ~/.config/bqtop/config.toml")
    ap.add_argument("-y", "--yes", action="store_true", help="with --init: no questions, write the example config")
    ap.add_argument("--debug", action="store_true", help="show full tracebacks")
    ap.add_argument("--version", action="version", version=f"bqtop {__version__}")
    args = ap.parse_args(argv)

    if args.init:
        from bqtop.wizard import run

        return run(Path(args.config) if args.config else None, assume_yes=args.yes)

    if args.demo:
        cfg = Config.demo()
    else:
        try:
            cfg = load(args.config)
        except ConfigError as e:
            print(f"bqtop: {e}", file=sys.stderr)
            return 2
    if args.window:
        cfg.ui.window_hours = args.window
        cfg.ui.max_window_hours = max(cfg.ui.max_window_hours, args.window)

    from bqtop.sources import make_source

    source = make_source(cfg)

    if args.check:
        return _check(cfg, source)

    if args.once or args.json or args.watch:
        from rich.console import Console

        from bqtop.render import render_once
        from bqtop.store import JobStore

        store = JobStore(
            source,
            cfg.pricing,
            tz=cfg.ui.timezone,
            max_hours=cfg.ui.max_window_hours,
            stream_rows=cfg.ui.stream_rows,
            top_n=cfg.ui.top_n,
            buckets=cfg.ui.timeline_buckets,
        )
        console = Console()
        while True:
            store.refresh(cfg.ui.window_hours)
            snap = store.snapshot(cfg.ui.window_hours, args.filter)
            if args.json:
                out = _to_dict(snap)
                out["totals"] = _to_dict(snap.totals)
                json.dump(out, sys.stdout, default=str, indent=1)
                print()
            else:
                if args.watch:
                    console.clear()
                render_once(snap, cfg, source.describe(), console=console, sort=args.sort)
            if not args.watch:
                return 0
            time.sleep(args.watch)

    from bqtop.app import BqTop

    BqTop(cfg, source).run()
    return 0


def _check(cfg: Config, source) -> int:
    print(f"config    {cfg.path or '(demo)'}")
    print(f"source    {source.describe()}")
    print(f"pricing   {cfg.pricing.describe()}")
    if cfg.quotas:
        print(f"quotas    {len(cfg.quotas)} project cap(s)")
    if cfg.budgets:
        print(f"budgets   {len(cfg.budgets)} daily budget(s)")
    ok_all = True
    for what, ok, detail in source.probe():
        mark = "✓" if ok else "✗"
        print(f"{mark} {what:<8} {detail}")
        if not ok:
            ok_all = False
            for needle, hint in HINTS:
                if needle.lower() in detail.lower():
                    print(f"  → {hint}")
    if ok_all:
        print("all good. run `bqtop` for the TUI or `bqtop --once` for a snapshot.")
    return 0 if ok_all else 1


def _to_dict(obj):
    if is_dataclass(obj):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


if __name__ == "__main__":
    sys.exit(main())

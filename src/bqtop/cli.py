from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from bqtop import __version__
from bqtop.config import ConfigError, default_config_path, load

EXAMPLE = Path(__file__).with_name("config.example.toml")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bqtop", description="htop for BigQuery: jobs, principals, projects, cost.")
    ap.add_argument("-c", "--config", help="config file (default: ./bqtop.toml, then ~/.config/bqtop/config.toml)")
    ap.add_argument("-w", "--window", type=float, help="window in hours (overrides config)")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--json", action="store_true", help="with --once: emit the snapshot as JSON")
    ap.add_argument("--sort", default="cost", choices=("cost", "bytes", "jobs", "errors", "slots"))
    ap.add_argument("--init", action="store_true", help="write a starter config to ~/.config/bqtop/config.toml")
    ap.add_argument("--version", action="version", version=f"bqtop {__version__}")
    args = ap.parse_args(argv)

    if args.init:
        return _init(args.config)

    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"bqtop: {e}", file=sys.stderr)
        return 2
    if args.window:
        cfg.ui.window_hours = args.window

    from bqtop.sources import make_source

    source = make_source(cfg)

    if args.once or args.json:
        snap = source.fetch()
        if args.json:
            out = {
                "fetched_at": snap.fetched_at.isoformat(), "window_hours": snap.window_hours,
                "totals": asdict(snap.totals), "by_principal": [asdict(a) for a in snap.by_principal],
                "by_project": [asdict(a) for a in snap.by_project], "by_table": [asdict(a) for a in snap.by_table],
                "jobs": [asdict(j) for j in snap.jobs],
            }
            json.dump(out, sys.stdout, default=str, indent=1)
            print()
        else:
            from bqtop.render import render_once

            render_once(snap, cfg, source.describe(), sort=args.sort)
        return 0

    from bqtop.app import BqTop

    BqTop(cfg, source).run()
    return 0


def _init(path: str | None) -> int:
    dest = Path(path) if path else default_config_path()
    if dest.exists():
        print(f"bqtop: {dest} already exists, not overwriting", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE, dest)
    print(f"wrote {dest}. Edit [source] and run `bqtop --once` to check the connection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

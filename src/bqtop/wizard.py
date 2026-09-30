"""Interactive `bqtop --init`: detects sensible defaults, asks the few real choices, writes the config
and offers to run the connectivity check."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from bqtop.config import default_config_path

EXAMPLE = Path(__file__).with_name("config.example.toml")


def detect_project() -> str:
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=8, check=False
        ).stdout.strip()
        if out and out != "(unset)":
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        import google.auth

        _, project = google.auth.default()
        return project or ""
    except Exception:
        return ""


def detect_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text().strip() or "UTC"
    except OSError:
        return "UTC"


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _toml_list(xs: list[str]) -> str:
    return "[" + ", ".join(f'"{x}"' for x in xs) + "]"


def run(dest: Path | None = None, assume_yes: bool = False) -> int:
    console = Console()
    dest = dest or default_config_path()
    if dest.exists():
        console.print(f"[red]bqtop:[/red] {dest} already exists, not overwriting")
        return 1

    if assume_yes or not sys.stdin.isatty():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE, dest)
        console.print(f"wrote {dest} (edit the source section, then run `bqtop --check`)")
        return 0

    console.print(
        Panel(
            "Let's point bqtop at your BigQuery. Enter accepts the default in brackets.\n"
            "[dim]Try `bqtop --demo` first if you just want to see the UI.[/dim]",
            title="bqtop setup",
        )
    )

    kind = Prompt.ask(
        "Source  [dim]information_schema = real time incl. running jobs · audit_log = a routed audit-log table[/dim]",
        choices=["information_schema", "audit_log"],
        default="information_schema",
    )
    detected = detect_project()
    billing = Prompt.ask("Billing project  [dim]runs and pays for bqtop's own queries[/dim]", default=detected or None)
    regions = _list(Prompt.ask("BigQuery region(s), comma separated", default="us"))

    scope, projects, table = "project", [billing], ""
    if kind == "information_schema":
        scope = Prompt.ask(
            "Scope  [dim]project = listed projects · folder = every project under the billing project's folder · "
            "organization · user = only your own jobs[/dim]",
            choices=["project", "folder", "organization", "user"],
            default="project",
        )
        if scope == "project":
            projects = _list(Prompt.ask("Projects to watch, comma separated", default=billing))
    else:
        table = Prompt.ask("Audit-log table  [dim]project.dataset.cloudaudit_googleapis_com_data_access[/dim]")

    mode = Prompt.ask(
        "Pricing  [dim]auto = slot price for jobs in a reservation, on-demand otherwise[/dim]",
        choices=["auto", "on_demand", "slots"],
        default="auto",
    )
    slot_price = 0.06
    if mode in ("auto", "slots"):
        slot_price = float(
            Prompt.ask(
                "Slot price USD/slot-hour  [dim]standard 0.04 · enterprise 0.06 · plus 0.10[/dim]", default="0.06"
            )
        )
    tz = Prompt.ask("Timezone for 'today' and clocks", default=detect_timezone())
    refresh = IntPrompt.ask("Refresh every N seconds", default=60 if kind == "information_schema" else 120)

    lines = [
        "# bqtop configuration, written by `bqtop --init`. Full reference:",
        "# https://github.com/dadadima/bqtop/blob/main/examples/config.example.toml",
        "",
        "[source]",
        f'kind = "{kind}"',
        f'billing_project = "{billing}"',
        f"regions = {_toml_list(regions)}",
    ]
    if kind == "information_schema":
        lines.append(f'scope = "{scope}"')
        if scope == "project":
            lines.append(f"projects = {_toml_list(projects)}")
    else:
        lines.append(f'table = "{table}"')
    lines += [
        "",
        "[pricing]",
        f'mode = "{mode}"',
        "on_demand_usd_per_tib = 6.25",
        f"slot_usd_per_hour = {slot_price}",
        "# [pricing.projects]",
        '# "my-dbt-project" = { mode = "slots", slot_usd_per_hour = 0.04 }',
        "",
        "[quotas]",
        "# daily QueryUsagePerDay caps per project, shown as % used today",
        '# "my-agents-project" = "5 TiB"',
        "",
        "[budgets]",
        "# daily USD budgets by principal or project",
        '# "svc-airflow@my-ingest.iam.gserviceaccount.com" = 50',
        "",
        "[ui]",
        f"refresh_seconds = {refresh}",
        "window_hours = 24",
        "max_window_hours = 168",
        f'timezone = "{tz}"',
        "top_n = 15",
        "stream_rows = 40",
        "",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines))
    console.print(f"\n[green]wrote[/green] {dest}")

    if Confirm.ask("Run `bqtop --check` now?", default=True):
        from bqtop.cli import main

        return main(["--check", "-c", str(dest)])
    console.print("next: `bqtop --check`, then `bqtop`.")
    return 0

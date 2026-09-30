"""Interactive `bqtop --init`. Six short questions, numbered choices, sensible defaults detected from the
machine, then the connectivity check."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.prompt import Confirm, Prompt

from bqtop import gcp
from bqtop.config import default_config_path

EXAMPLE = Path(__file__).with_name("config.example.toml")

SCOPES = [  # (value, label)
    ("folder", "a folder: every project in it, sub-folders included"),
    ("project", "one or more projects"),
    ("organization", "the whole organization"),
    ("user", "just my own jobs"),
]
SOURCES = [
    ("information_schema", "INFORMATION_SCHEMA: live, includes running jobs"),
    ("audit_log", "an audit-log sink table (completed jobs only)"),
]
PRICINGS = [
    ("on_demand", "on-demand, $6.25 per TiB scanned"),
    ("slots", "editions / reservations, priced per slot-hour"),
    ("auto", "auto: slot price when the job used a reservation, on-demand otherwise"),
]


# ---- detection -----------------------------------------------------------------------------------
def detect_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz and _valid_tz(tz):
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


def _valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


# ---- prompts -------------------------------------------------------------------------------------
def choose(console: Console, question: str, options: list[tuple[str, str]], default: str) -> str:
    """Numbered menu. Accepts the number, the value, or a loose spelling (on-demand, On_Demand, ...)."""
    console.print(f"\n[bold]{question}[/bold]")
    for i, (value, label) in enumerate(options, 1):
        mark = "[green]›[/green]" if value == default else " "
        console.print(f"  {mark} [bold]{i}[/bold]  {label}")
    idx = {v: str(i) for i, (v, _) in enumerate(options, 1)}
    while True:
        raw = Prompt.ask("  choice", default=idx[default], show_default=True).strip().lower().replace("-", "_")
        if raw in idx.values():
            return options[int(raw) - 1][0]
        for value, _ in options:
            if raw == value or raw == value.replace("_", ""):
                return value
        console.print(f"  [red]pick 1-{len(options)}[/red]")


def ask(console: Console, question: str, default: str | None = None, hint: str = "", validate=None) -> str:
    while True:
        console.print(f"\n[bold]{question}[/bold]" + (f"  [dim]{hint}[/dim]" if hint else ""))
        value = Prompt.ask("  ", default=default, show_default=bool(default)).strip()
        if not value and default:
            value = default
        if not value:
            console.print("  [red]required[/red]")
            continue
        if validate:
            err = validate(value)
            if err:
                console.print(f"  [red]{err}[/red]")
                continue
        return value


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _toml_list(xs: list[str]) -> str:
    return "[" + ", ".join(f'"{x}"' for x in xs) + "]"


def pick_folder(console: Console, detected_project: str) -> tuple[str, str, str, str]:
    """Ask which folder to watch. Offers the folders above the default project, probes which of them the
    current identity can actually watch, defaults to the widest one, and finds the project inside it to run
    from. Returns (runner project, folder id, label, region)."""
    chain = [n for n in gcp.ancestors(detected_project) if n.kind == "folder"] if detected_project else []
    runners: dict[str, tuple[str, str]] = {}
    if chain:
        console.print(f"\n  [dim]checking which folders above {detected_project} you can watch…[/dim]")
        for n in chain:
            runners[n.id] = gcp.find_runner(n.id)
        options = []
        for i, n in enumerate(chain):
            if runners[n.id][0]:
                tag = "  [green]✓ can watch[/green]"
            elif runners[n.id][1] == "empty":
                tag = "  [dim]no projects directly inside[/dim]"
            else:
                tag = "  [dim]no access[/dim]"
            here = f"  ← contains {detected_project}" if i == 0 else ""
            options.append((n.id, f"{n.label}{here}{tag}"))
        options.append(("other", "another folder (type its id)"))
        watchable = [n.id for n in chain if runners[n.id][0]]
        default = watchable[-1] if watchable else chain[0].id
        choice = choose(console, "2/6  Which folder?", options, default)
        folder_id = choice if choice != "other" else ask(console, "     Folder id", hint="digits, e.g. 123456789012")
    else:
        folder_id = ask(
            console,
            "2/6  Which folder? (id)",
            hint="digits, e.g. 123456789012 · `gcloud resource-manager folders list --organization=…`",
        )
    label = next((n.label for n in chain if n.id == folder_id), "") or gcp.folder_name(folder_id) or folder_id

    runner, region = runners.get(folder_id) or gcp.find_runner(folder_id)
    if region == "empty":
        region = "us"
    if runner:
        console.print(
            f"  [green]→[/green] runs from [bold]{runner}[/bold], watches [bold]{label}[/bold] and its sub-folders"
        )
    else:
        console.print(
            f"  [yellow]→[/yellow] found no project directly inside {label} where you can run jobs and list "
            "the folder's jobs (roles/bigquery.jobUser + roles/bigquery.resourceViewer)"
        )
        runner = ask(
            console,
            "     Project directly inside that folder to run bqtop's queries from",
            hint="BigQuery exposes a folder's jobs only through a project that sits right under it",
        )
        region = "us"
    return runner, folder_id, label, region


# ---- main ----------------------------------------------------------------------------------------
def run(dest: Path | None = None, assume_yes: bool = False) -> int:
    console = Console(highlight=False)
    dest = dest or default_config_path()

    if assume_yes or not sys.stdin.isatty():
        if dest.exists():
            console.print(f"[red]bqtop:[/red] {dest} already exists, not overwriting")
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE, dest)
        console.print(f"wrote {dest} (edit the source section, then run `bqtop --check`)")
        return 0

    console.print("[bold]bqtop setup[/bold]  [dim]enter = default · ctrl-c = abort · `bqtop --demo` to just look[/dim]")
    if dest.exists() and not Confirm.ask(f"\n{dest} exists. Overwrite?", default=False):
        return 1

    # 1. what to watch
    scope = choose(console, "1/6  What should bqtop watch?", SCOPES, "folder")

    # 2. what exactly, and which project runs the queries
    detected = gcp.default_project()
    folder_id, folder_label, region_default = "", "", "us"
    if scope == "folder":
        billing, folder_id, folder_label, region_default = pick_folder(console, detected)
    elif scope == "organization":
        billing = ask(
            console,
            "2/6  Project to run bqtop's queries from",
            default=detected or None,
            hint="any project in the organization you can run jobs in",
        )
    else:
        billing = ask(
            console,
            "2/6  Project to run bqtop's queries from",
            default=detected or None,
            hint="bqtop bills its own small queries here",
        )
    projects = [billing]
    if scope == "project":
        projects = _list(ask(console, "     Projects to watch, comma separated", default=billing))

    # 3. source
    source = choose(console, "3/6  Where should the job data come from?", SOURCES, "information_schema")
    table = ""
    if source == "audit_log":
        table = ask(console, "     Audit-log table", hint="project.dataset.cloudaudit_googleapis_com_data_access")

    # 4. region, 5. pricing, 6. timezone + refresh
    regions = _list(
        ask(console, "4/6  BigQuery region(s)", default="us", hint="us, eu, europe-west1, … comma separated")
    )
    pricing = choose(console, "5/6  How is BigQuery billed?", PRICINGS, "on_demand")
    slot_price = "0.06"
    if pricing in ("slots", "auto"):
        slot_price = ask(
            console,
            "     Slot price, USD per slot-hour",
            default="0.06",
            hint="standard 0.04 · enterprise 0.06 · enterprise plus 0.10",
            validate=lambda v: None if v.replace(".", "", 1).isdigit() else "a number, e.g. 0.06",
        )
    tz = ask(
        console,
        "6/6  Timezone for 'today' and clocks",
        default=detect_timezone(),
        validate=lambda v: None if _valid_tz(v) else f"unknown timezone {v!r}, e.g. Europe/Brussels or UTC",
    )
    refresh = ask(
        console,
        "     Refresh every N seconds",
        default="30" if source == "information_schema" else "120",
        validate=lambda v: None if v.isdigit() and int(v) >= 5 else "a whole number of seconds, 5 or more",
    )

    lines = [
        "# bqtop configuration, written by `bqtop --init`. Reference with all options:",
        "# https://github.com/dadadima/bqtop/blob/main/examples/config.example.toml",
        "",
        "[source]",
        f'kind = "{source}"',
        f'billing_project = "{billing}"',
        f"regions = {_toml_list(regions)}",
    ]
    if source == "information_schema":
        lines.append(f'scope = "{scope}"')
        if scope == "folder" and folder_id:
            lines.append(f'folder = "{folder_id}"  # {folder_label}')
        if scope == "project":
            lines.append(f"projects = {_toml_list(projects)}")
    else:
        lines.append(f'table = "{table}"')
    lines += [
        "",
        "[pricing]",
        f'mode = "{pricing}"',
        "on_demand_usd_per_tib = 6.25",
        f"slot_usd_per_hour = {slot_price}",
        "",
        "[quotas]",
        "# daily QueryUsagePerDay caps per project, shown as % used today, e.g.",
        '# "my-agents-project" = "5 TiB"',
        "",
        "[budgets]",
        "# daily USD budgets by principal or project, e.g.",
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
    console.print(f"\n[green]wrote[/green] {dest}\n")

    from bqtop.cli import main

    rc = main(["--check", "-c", str(dest)])
    if rc == 0:
        console.print("\nrun [bold]bqtop[/bold] to start. Add [quotas] / [budgets] to the config whenever you like.")
    return rc

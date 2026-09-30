"""Interactive `bqtop --init`. Six short questions, numbered choices, sensible defaults detected from the
machine, then the connectivity check."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.console import Console
from rich.prompt import Confirm, Prompt

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
def _gcloud(*args: str) -> str:
    try:
        r = subprocess.run(
            ["gcloud", *args, "--format=value(.)"], capture_output=True, text=True, timeout=8, check=False
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def detect_project() -> str:
    out = _gcloud("config", "get-value", "project")
    if out and out != "(unset)":
        return out
    try:
        import google.auth

        _, project = google.auth.default()
        return project or ""
    except Exception:
        return ""


def detect_parent(project: str) -> tuple[str, str, str]:
    """(parent type, parent id, display name) of a project via gcloud, else the Resource Manager API with the
    machine's Google credentials; empty strings when neither works."""
    ptype, pid, name = _parent_via_gcloud(project)
    if not ptype:
        ptype, pid, name = _parent_via_api(project)
    return ptype, pid, name


def _parent_via_gcloud(project: str) -> tuple[str, str, str]:
    try:
        r = subprocess.run(
            ["gcloud", "projects", "describe", project, "--format=value(parent.type,parent.id)"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        parts = r.stdout.split()
        if r.returncode != 0 or len(parts) != 2:
            return "", "", ""
        ptype, pid = parts
        name = ""
        if ptype == "folder":
            r2 = subprocess.run(
                ["gcloud", "resource-manager", "folders", "describe", pid, "--format=value(displayName)"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            name = r2.stdout.strip() if r2.returncode == 0 else ""
        return ptype, pid, name
    except (OSError, subprocess.TimeoutExpired):
        return "", "", ""


def _parent_via_api(project: str) -> tuple[str, str, str]:
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        s = AuthorizedSession(creds)
        r = s.get(f"https://cloudresourcemanager.googleapis.com/v3/projects/{project}", timeout=8)
        if r.status_code != 200:
            return "", "", ""
        parent = r.json().get("parent", "")  # "folders/123" or "organizations/456"
        if "/" not in parent:
            return "", "", ""
        kind, pid = parent.split("/", 1)
        ptype = {"folders": "folder", "organizations": "organization"}.get(kind, kind)
        name = ""
        if ptype == "folder":
            r2 = s.get(f"https://cloudresourcemanager.googleapis.com/v3/folders/{pid}", timeout=8)
            name = r2.json().get("displayName", "") if r2.status_code == 200 else ""
        return ptype, pid, name
    except Exception:
        return "", "", ""


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

    # 2. billing project (+ folder detection for folder scope)
    detected = detect_project()
    if scope == "folder":
        hint = "bqtop runs its queries here; it watches the folder that directly contains this project"
    elif scope == "organization":
        hint = "bqtop runs its queries here; it watches the whole organization"
    else:
        hint = "bqtop runs its queries here"
    billing = ask(console, "2/6  Project to run bqtop's queries from", default=detected or None, hint=hint)
    if scope == "folder":
        ptype, pid, name = detect_parent(billing)
        if ptype == "folder":
            console.print(f"  [green]→[/green] watches folder [bold]{name or pid}[/bold] ({pid}) and its sub-folders")
        elif ptype == "organization":
            console.print(
                "  [yellow]→[/yellow] this project sits directly under the organization; "
                "use scope 3 (organization) or pick a project inside the folder you want"
            )
        elif billing:
            console.print(
                "  [dim]→ could not look up the project's folder (gcloud not available or no permission); "
                "`bqtop --check` will show how many projects are covered[/dim]"
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

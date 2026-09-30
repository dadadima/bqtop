"""Rich rendering for `bqtop --once` (and the text the TUI reuses for its summary line)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bqtop import fmt
from bqtop.config import Config
from bqtop.model import Snapshot

SORT_KEYS = ("cost", "bytes", "jobs", "errors", "slots")


def sort_aggs(aggs, sort: str):
    key = {
        "cost": lambda a: a.bytes_billed, "bytes": lambda a: a.bytes_processed,
        "jobs": lambda a: a.jobs, "errors": lambda a: a.errors, "slots": lambda a: a.slot_ms,
    }[sort]
    return sorted(aggs, key=lambda a: (key(a), a.jobs), reverse=True)


def summary_text(snap: Snapshot, cfg: Config, source_desc: str) -> Text:
    t = snap.totals
    tz = ZoneInfo(cfg.ui.timezone)
    price = cfg.price_per_tib
    line = Text()
    line.append(f" window {snap.window_hours:g}h ", style="bold")
    line.append(f"jobs {t.jobs:,} ")
    if t.running:
        line.append(f"running {t.running} ", style="bold yellow")
    if t.errors:
        line.append(f"errors {t.errors} ", style="bold red")
    line.append(f"| billed {fmt.bytes_(t.bytes_billed)} = {fmt.money(t.cost(price))} ")
    line.append(f"| today {fmt.money(t.cost_today(price))} ")
    line.append(f"| slot-h {fmt.slot_hours(t.slot_ms)} ")
    line.append(f"| principals {t.principals} projects {len(snap.by_project)} ")
    line.append(f"| {fmt.clock(snap.fetched_at, tz)} {cfg.ui.timezone} ", style="dim")
    line.append(f"| {source_desc} ", style="dim")
    line.append(f"| bqtop used {fmt.bytes_(snap.query_bytes_billed)}", style="dim")
    return line


def principals_table(snap: Snapshot, cfg: Config, sort: str = "cost") -> Table:
    tb = Table(title=f"principals (by {sort})", expand=True, pad_edge=False)
    for col, just in (("principal", "left"), ("jobs", "right"), ("run", "right"), ("err", "right"),
                      ("billed", "right"), ("cost", "right"), ("slot-h", "right")):
        tb.add_column(col, justify=just, no_wrap=True)
    for a in sort_aggs(snap.by_principal, sort)[: cfg.ui.top_n]:
        tb.add_row(
            fmt.short_principal(a.key), f"{a.jobs:,}", str(a.running or ""), _err(a.errors),
            fmt.bytes_(a.bytes_billed), fmt.money(a.cost(cfg.price_per_tib)), fmt.slot_hours(a.slot_ms),
        )
    return tb


def projects_table(snap: Snapshot, cfg: Config, sort: str = "cost") -> Table:
    tb = Table(title=f"projects (by {sort})", expand=True, pad_edge=False)
    for col, just in (("project", "left"), ("jobs", "right"), ("run", "right"), ("err", "right"),
                      ("billed", "right"), ("cost", "right"), ("today", "right"), ("quota", "right")):
        tb.add_column(col, justify=just, no_wrap=True)
    for a in sort_aggs(snap.by_project, sort)[: cfg.ui.top_n]:
        cap = cfg.quotas.get(a.key)
        quota = _quota_cell(a.bytes_billed_today, cap)
        tb.add_row(
            a.key, f"{a.jobs:,}", str(a.running or ""), _err(a.errors), fmt.bytes_(a.bytes_billed),
            fmt.money(a.cost(cfg.price_per_tib)), fmt.money(a.cost_today(cfg.price_per_tib)), quota,
        )
    return tb


def tables_table(snap: Snapshot, cfg: Config) -> Table:
    tb = Table(title="hot tables (billed by jobs touching them)", expand=True, pad_edge=False)
    for col, just in (("table", "left"), ("jobs", "right"), ("who", "right"), ("billed", "right"), ("cost", "right")):
        tb.add_column(col, justify=just, no_wrap=True)
    for a in snap.by_table[: cfg.ui.top_n]:
        tb.add_row(a.key, f"{a.jobs:,}", str(a.principals), fmt.bytes_(a.bytes_billed),
                   fmt.money(a.cost(cfg.price_per_tib)))
    return tb


def jobs_table(snap: Snapshot, cfg: Config, width: int = 120) -> Table:
    tz = ZoneInfo(cfg.ui.timezone)
    tb = Table(title="jobs (running first, then newest)", expand=True, pad_edge=False)
    cols = (("time", "left", 8, 8), ("state", "left", 7, 7), ("principal", "left", 24, 40),
            ("project", "left", 20, 32), ("type", "left", 8, 14), ("dur", "right", 5, 6),
            ("billed", "right", 9, 10), ("cost", "right", 7, 9))
    for col, just, w, mx in cols:
        tb.add_column(col, justify=just, no_wrap=True, min_width=w, max_width=mx, overflow="ellipsis")
    tb.add_column("query", justify="left", no_wrap=True, overflow="ellipsis", ratio=1)
    qwidth = max(30, width - sum(w for _, _, w, _ in cols) - 12)
    for j in snap.jobs[: cfg.ui.stream_rows]:
        state = Text(j.state, style="yellow" if j.state == "RUNNING" else ("red" if j.error_code else ""))
        text = j.error_message if j.error_code else (j.query or j.destination_table or "")
        tb.add_row(
            fmt.clock(j.creation_time, tz), state, fmt.short_principal(j.principal), j.project_id,
            (j.statement_type or j.job_type or "-").lower(), fmt.duration(j.duration_s()),
            fmt.bytes_(j.bytes_billed), fmt.money(j.cost(cfg.price_per_tib)), fmt.one_line(text, qwidth),
        )
    return tb


def render_once(snap: Snapshot, cfg: Config, source_desc: str, console: Console | None = None,
                sort: str = "cost") -> None:
    console = console or Console()
    console.print(Panel(summary_text(snap, cfg, source_desc), title="bqtop", expand=True))
    console.print(Group(principals_table(snap, cfg, sort), projects_table(snap, cfg, sort),
                        tables_table(snap, cfg), jobs_table(snap, cfg, console.width)))


def _err(n: int) -> Text:
    return Text(str(n), style="bold red") if n else Text("")


def _quota_cell(used: int, cap: int | None) -> Text:
    if cap is None:
        return Text("")
    ratio = used / cap if cap else 0
    style = "bold red" if ratio >= 0.9 else ("yellow" if ratio >= 0.6 else "green")
    return Text(f"{ratio * 100:.0f}% of {fmt.bytes_(cap)}", style=style)

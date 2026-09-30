"""Rich rendering for `bqtop --once` / `--watch`, plus the pieces the TUI reuses (summary, timeline,
cap cells)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bqtop import fmt
from bqtop.config import Config
from bqtop.model import Agg, Snapshot

SORT_KEYS = ("cost", "bytes", "jobs", "errors", "slots")
_BARS = "▁▂▃▄▅▆▇█"


def sort_aggs(aggs: list[Agg], sort: str) -> list[Agg]:
    key = {
        "cost": lambda a: a.cost,
        "bytes": lambda a: a.bytes_processed,
        "jobs": lambda a: a.jobs,
        "errors": lambda a: a.errors,
        "slots": lambda a: a.slot_ms,
    }[sort]
    return sorted(aggs, key=lambda a: (key(a), a.jobs), reverse=True)


def summary_text(snap: Snapshot, cfg: Config, source_desc: str, paused: bool = False) -> Text:
    t = snap.totals
    tz = ZoneInfo(cfg.ui.timezone)
    line = Text()
    line.append(f" {snap.window_hours:g}h ", style="bold reverse")
    if snap.filter:
        line.append(f" filter «{snap.filter}» ", style="bold magenta")
    line.append(f" jobs {t.jobs:,}")
    if t.running:
        line.append(f"  running {t.running}", style="bold yellow")
    if t.errors:
        line.append(f"  errors {t.errors}", style="bold red")
    if t.jobs:
        line.append(f"  cache {t.cache_hits / t.jobs * 100:.0f}%", style="dim")
    line.append(f"  │ billed {fmt.bytes_(t.bytes_billed)} ≈ ", style="")
    line.append(fmt.money(t.cost), style="bold")
    line.append(f"  today {fmt.money(t.cost_today)}")
    line.append(f"  slot-h {fmt.slot_hours(t.slot_ms)}")
    line.append(f"  │ {len(t.principals)} principals · {len(snap.by_project)} projects")
    line.append(f"  │ {fmt.clock(snap.fetched_at, tz)} {cfg.ui.timezone}", style="dim")
    if paused:
        line.append("  PAUSED", style="bold yellow")
    return line


def footer_text(snap: Snapshot, cfg: Config, source_desc: str) -> Text:
    t = Text(style="dim")
    t.append(f"{source_desc} · pricing {cfg.pricing.describe()} · store {snap.store_rows:,} jobs")
    if snap.coverage_start:
        t.append(f" since {fmt.when(snap.coverage_start, ZoneInfo(cfg.ui.timezone))}")
    t.append(
        f" · refresh {snap.refresh_seconds:.1f}s, bqtop billed {fmt.bytes_(snap.refresh_bytes_billed)}"
        f" (session {fmt.bytes_(snap.session_bytes_billed)})"
    )
    return t


def timeline_text(snap: Snapshot) -> Text:
    peak = max(snap.timeline) if snap.timeline else 0
    bars = "".join(_BARS[min(7, int(v / peak * 7.999))] if peak else _BARS[0] for v in snap.timeline)
    t = Text()
    t.append(bars, style="cyan")
    t.append(f"  cost per {fmt.duration(snap.bucket_minutes * 60)} bucket, peak {fmt.money(peak)}", style="dim")
    return t


def cap_cell(a: Agg, cfg: Config, kind: str) -> Text:
    """Quota % (bytes/day, projects) or budget % (USD/day, principals or projects) for today."""
    if kind == "project" and a.key in cfg.quotas:
        cap = cfg.quotas[a.key]
        return _ratio(a.bytes_billed_today / cap if cap else 0, f"of {fmt.bytes_(cap)}/d")
    if a.key in cfg.budgets:
        cap = cfg.budgets[a.key]
        return _ratio(a.cost_today / cap if cap else 0, f"of {fmt.money(cap)}/d")
    return Text("")


def _ratio(ratio: float, suffix: str) -> Text:
    style = "bold red" if ratio >= 0.9 else ("yellow" if ratio >= 0.6 else "green")
    return Text(f"{ratio * 100:.0f}% {suffix}", style=style)


def err_cell(n: int) -> Text:
    return Text(str(n), style="bold red") if n else Text("")


def state_cell(state: str, error: bool) -> Text:
    return Text(state.lower(), style="yellow" if state != "DONE" else ("red" if error else "dim"))


# ---- tables (Rich) --------------------------------------------------------------------------------
def principals_table(snap: Snapshot, cfg: Config, sort: str = "cost") -> Table:
    tb = Table(title=f"principals · by {sort}", expand=True, pad_edge=False)
    for col, just in (
        ("principal", "left"),
        ("jobs", "right"),
        ("run", "right"),
        ("err", "right"),
        ("billed", "right"),
        ("cost", "right"),
        ("today", "right"),
        ("slot-h", "right"),
        ("budget", "right"),
    ):
        tb.add_column(col, justify=just, no_wrap=True, overflow="ellipsis")
    for a in sort_aggs(snap.by_principal, sort)[: cfg.ui.top_n]:
        tb.add_row(
            fmt.short_principal(a.key),
            f"{a.jobs:,}",
            _n(a.running),
            err_cell(a.errors),
            fmt.bytes_(a.bytes_billed),
            fmt.money(a.cost),
            fmt.money(a.cost_today),
            fmt.slot_hours(a.slot_ms),
            cap_cell(a, cfg, "principal"),
        )
    return tb


def projects_table(snap: Snapshot, cfg: Config, sort: str = "cost") -> Table:
    tb = Table(title=f"projects · by {sort}", expand=True, pad_edge=False)
    for col, just in (
        ("project", "left"),
        ("jobs", "right"),
        ("run", "right"),
        ("err", "right"),
        ("billed", "right"),
        ("cost", "right"),
        ("today", "right"),
        ("quota/budget", "right"),
    ):
        tb.add_column(col, justify=just, no_wrap=True, overflow="ellipsis")
    for a in sort_aggs(snap.by_project, sort)[: cfg.ui.top_n]:
        tb.add_row(
            a.key,
            f"{a.jobs:,}",
            _n(a.running),
            err_cell(a.errors),
            fmt.bytes_(a.bytes_billed),
            fmt.money(a.cost),
            fmt.money(a.cost_today),
            cap_cell(a, cfg, "project"),
        )
    return tb


def tables_table(snap: Snapshot, cfg: Config) -> Table:
    tb = Table(title="hot tables · billed by jobs touching them", expand=True, pad_edge=False)
    for col, just in (("table", "left"), ("jobs", "right"), ("who", "right"), ("billed", "right"), ("cost", "right")):
        tb.add_column(col, justify=just, no_wrap=True, overflow="ellipsis")
    for a in snap.by_table[: cfg.ui.top_n]:
        tb.add_row(a.key, f"{a.jobs:,}", str(len(a.principals)), fmt.bytes_(a.bytes_billed), fmt.money(a.cost))
    return tb


def models_table(snap: Snapshot, cfg: Config) -> Table:
    tb = Table(title="dbt models · from the query comment", expand=True, pad_edge=False)
    for col, just in (
        ("model", "left"),
        ("runs", "right"),
        ("err", "right"),
        ("billed", "right"),
        ("cost", "right"),
        ("slot-h", "right"),
    ):
        tb.add_column(col, justify=just, no_wrap=True, overflow="ellipsis")
    for a in snap.by_model[: cfg.ui.top_n]:
        tb.add_row(
            fmt.dbt_node(a.key),
            f"{a.jobs:,}",
            err_cell(a.errors),
            fmt.bytes_(a.bytes_billed),
            fmt.money(a.cost),
            fmt.slot_hours(a.slot_ms),
        )
    return tb


def jobs_table(snap: Snapshot, cfg: Config, width: int = 120) -> Table:
    tz = ZoneInfo(cfg.ui.timezone)
    tb = Table(title="jobs · running first, then newest", expand=True, pad_edge=False)
    cols = (
        ("time", "left", 8, 8),
        ("state", "left", 7, 7),
        ("principal", "left", 24, 40),
        ("project", "left", 20, 32),
        ("type", "left", 8, 14),
        ("dur", "right", 5, 6),
        ("billed", "right", 9, 10),
        ("cost", "right", 7, 9),
    )
    for col, just, w, mx in cols:
        tb.add_column(col, justify=just, no_wrap=True, min_width=w, max_width=mx, overflow="ellipsis")
    tb.add_column("query", justify="left", no_wrap=True, overflow="ellipsis", ratio=1)
    qwidth = max(30, width - sum(w for _, _, w, _ in cols) - 12)
    for j in snap.jobs[: cfg.ui.stream_rows]:
        text = j.error_message if j.error_code else (j.query or j.destination_table or "")
        tb.add_row(
            fmt.clock(j.creation_time, tz),
            state_cell(j.state, bool(j.error_code)),
            fmt.short_principal(j.principal),
            j.project_id,
            fmt.stmt(j),
            fmt.duration(j.duration_s()),
            fmt.bytes_(j.bytes_billed),
            fmt.money(j.cost),
            fmt.one_line(text, qwidth),
        )
    return tb


def render_once(
    snap: Snapshot, cfg: Config, source_desc: str, console: Console | None = None, sort: str = "cost"
) -> None:
    console = console or Console()
    console.print(
        Panel(
            Group(summary_text(snap, cfg, source_desc), timeline_text(snap), footer_text(snap, cfg, source_desc)),
            title="bqtop",
            expand=True,
        )
    )
    console.print(
        Group(
            principals_table(snap, cfg, sort),
            projects_table(snap, cfg, sort),
            tables_table(snap, cfg),
            jobs_table(snap, cfg, console.width),
        )
    )


def _n(n: int) -> str:
    return f"{n:,}" if n else ""

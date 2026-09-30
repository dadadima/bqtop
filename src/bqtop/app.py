"""Textual TUI: header summary, principals + projects, hot tables, job stream."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bqtop import fmt
from bqtop.config import Config
from bqtop.model import Snapshot
from bqtop.render import SORT_KEYS, sort_aggs, summary_text
from bqtop.sources.base import Source

WINDOWS = (1, 6, 24, 72, 168)


class BqTop(App):
    TITLE = "bqtop"
    CSS = """
    #summary { height: auto; padding: 0 1; background: $panel; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    DataTable { border: round $primary; height: 1fr; }
    #top { height: 45%; }
    #bottom { height: 55%; }
    #principals, #projects { width: 1fr; }
    #tables { width: 2fr; }
    #jobs { width: 3fr; }
    """
    BINDINGS = [
        ("q", "quit", "quit"),
        ("r", "refresh_now", "refresh"),
        ("w", "cycle_window", "window"),
        ("s", "cycle_sort", "sort"),
        ("j", "toggle_jobs", "jobs/tables"),
    ]

    def __init__(self, cfg: Config, source: Source) -> None:
        super().__init__()
        self.cfg = cfg
        self.source = source
        self.window_hours = cfg.ui.window_hours
        self.sort = "cost"
        self.snap: Snapshot | None = None
        self.wide_jobs = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        yield Static("starting…", id="status")
        with Vertical():
            with Horizontal(id="top"):
                yield DataTable(id="principals", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="projects", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="bottom"):
                yield DataTable(id="tables", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#principals", DataTable).border_title = "principals"
        self.query_one("#projects", DataTable).border_title = "projects"
        self.query_one("#tables", DataTable).border_title = "hot tables"
        self.query_one("#jobs", DataTable).border_title = "jobs"
        self.set_interval(self.cfg.ui.refresh_seconds, self.action_refresh_now)
        self.action_refresh_now()

    # ---- actions -------------------------------------------------------------------------------
    def action_refresh_now(self) -> None:
        self.query_one("#status", Static).update(Text("refreshing…", style="yellow"))
        self.load_snapshot()

    def action_cycle_window(self) -> None:
        i = WINDOWS.index(self.window_hours) if self.window_hours in WINDOWS else -1
        self.window_hours = WINDOWS[(i + 1) % len(WINDOWS)]
        self.action_refresh_now()

    def action_cycle_sort(self) -> None:
        self.sort = SORT_KEYS[(SORT_KEYS.index(self.sort) + 1) % len(SORT_KEYS)]
        if self.snap:
            self.render_snapshot(self.snap)

    def action_toggle_jobs(self) -> None:
        self.wide_jobs = not self.wide_jobs
        self.query_one("#tables", DataTable).display = not self.wide_jobs
        if self.snap:
            self.render_snapshot(self.snap)

    # ---- data ----------------------------------------------------------------------------------
    @work(thread=True, exclusive=True)
    def load_snapshot(self) -> None:
        try:
            snap = self.source.fetch(self.window_hours)
        except Exception as e:  # surface, keep the last good snapshot on screen
            self.call_from_thread(self._show_error, e)
            return
        self.call_from_thread(self._apply, snap)

    def _apply(self, snap: Snapshot) -> None:
        self.snap = snap
        self.render_snapshot(snap)
        self.query_one("#status", Static).update(
            Text(f"ok · next refresh in {self.cfg.ui.refresh_seconds}s · keys: q quit, r refresh, "
                 f"w window, s sort, j jobs/tables", style="dim")
        )

    def _show_error(self, e: Exception) -> None:
        self.query_one("#status", Static).update(Text(f"error: {fmt.one_line(str(e), 200)}", style="bold red"))

    # ---- rendering -----------------------------------------------------------------------------
    def render_snapshot(self, snap: Snapshot) -> None:
        cfg, price = self.cfg, self.cfg.price_per_tib
        tz = ZoneInfo(cfg.ui.timezone)
        self.query_one("#summary", Static).update(summary_text(snap, cfg, self.source.describe()))

        t = self.query_one("#principals", DataTable)
        t.border_title = f"principals · by {self.sort}"
        _reset(t, ("principal", "jobs", "run", "err", "billed", "cost", "slot-h"))
        for a in sort_aggs(snap.by_principal, self.sort)[: cfg.ui.top_n]:
            t.add_row(fmt.short_principal(a.key), _n(a.jobs), _n(a.running), _err(a.errors),
                      fmt.bytes_(a.bytes_billed), fmt.money(a.cost(price)), fmt.slot_hours(a.slot_ms))

        t = self.query_one("#projects", DataTable)
        t.border_title = f"projects · by {self.sort}"
        _reset(t, ("project", "jobs", "run", "err", "billed", "cost", "today", "quota"))
        for a in sort_aggs(snap.by_project, self.sort)[: cfg.ui.top_n]:
            t.add_row(a.key, _n(a.jobs), _n(a.running), _err(a.errors), fmt.bytes_(a.bytes_billed),
                      fmt.money(a.cost(price)), fmt.money(a.cost_today(price)),
                      _quota(a.bytes_billed_today, cfg.quotas.get(a.key)))

        t = self.query_one("#tables", DataTable)
        _reset(t, ("table", "jobs", "who", "billed", "cost"))
        for a in snap.by_table[: cfg.ui.top_n]:
            t.add_row(a.key, _n(a.jobs), _n(a.principals), fmt.bytes_(a.bytes_billed), fmt.money(a.cost(price)))

        t = self.query_one("#jobs", DataTable)
        t.border_title = "jobs · running first, then newest" if self.source.has_running_jobs else "jobs · newest"
        _reset(t, ("time", "state", "principal", "project", "type", "dur", "billed", "cost", "query"))
        qwidth = max(30, (t.size.width or 120) - 105)
        for j in snap.jobs[: cfg.ui.stream_rows]:
            text = j.error_message if j.error_code else (j.query or j.destination_table or "")
            state = Text(j.state, style="yellow" if j.state == "RUNNING" else ("red" if j.error_code else ""))
            t.add_row(fmt.clock(j.creation_time, tz), state, fmt.short_principal(j.principal), j.project_id,
                      (j.statement_type or j.job_type or "-").lower(), fmt.duration(j.duration_s()),
                      fmt.bytes_(j.bytes_billed), fmt.money(j.cost(price)), fmt.one_line(text, qwidth))


def _reset(t: DataTable, cols: tuple[str, ...]) -> None:
    t.clear(columns=True)
    t.add_columns(*cols)


def _n(n: int) -> str:
    return f"{n:,}" if n else ""


def _err(n: int) -> Text:
    return Text(str(n), style="bold red") if n else Text("")


def _quota(used: int, cap: int | None) -> Text:
    if cap is None:
        return Text("")
    ratio = used / cap if cap else 0
    style = "bold red" if ratio >= 0.9 else ("yellow" if ratio >= 0.6 else "green")
    return Text(f"{ratio * 100:.0f}% of {fmt.bytes_(cap)}", style=style)

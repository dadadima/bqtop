"""Textual TUI: summary + cost timeline, principals / projects, hot tables / job stream, with filter,
drill-down, job detail, help and pause."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Sparkline, Static

from bqtop import fmt
from bqtop.config import Config
from bqtop.model import Job, Snapshot
from bqtop.render import SORT_KEYS, cap_cell, err_cell, footer_text, sort_aggs, state_cell, summary_text
from bqtop.sources.base import Source
from bqtop.store import JobStore

WINDOWS = (1, 6, 24, 72, 168)

HELP = """\
[b]bqtop[/b] · htop for BigQuery

[b]q[/b]      quit                      [b]r[/b]  refresh now
[b]w[/b]      wider window (1h → 6h → 24h → 72h → 168h → 1h)
[b]s[/b]      cycle sort: cost, bytes, jobs, errors, slots
[b]j[/b]      hide/show the tables panel (widens the job stream)
[b]d[/b]      tables panel: hot tables ↔ dbt models (cost per model, from dbt's query comment)
[b]/[/b]      filter (principal, project, table, query text, error)
[b]esc[/b]    clear filter / close dialog
[b]p[/b]      pause auto-refresh
[b]enter[/b]  on a principal, project or table: drill down (filter on it)
       on a job: details with the full query text
[b]tab[/b]    move between panels · arrows to move inside

[dim]cost = bytes billed × on-demand price, or slot-hours × slot price for jobs that ran in a
reservation (see [pricing] in the config). "today" is since local midnight and is what the
quota/budget percentages compare against.[/dim]
"""


class BqTop(App):
    TITLE = "bqtop"
    AUTO_FOCUS = "#jobs"
    CSS = """
    #summary { height: auto; padding: 0 1; background: $panel; }
    #timeline { height: 2; margin: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    #filter { display: none; margin: 0 1; }
    DataTable { border: round $primary; height: 1fr; }
    DataTable:focus { border: round $accent; }
    #top { height: 42%; }
    #bottom { height: 58%; }
    #principals { width: 3fr; }
    #projects { width: 2fr; }
    #tables { width: 2fr; }
    #jobs { width: 3fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("r", "refresh_now", "refresh"),
        Binding("w", "cycle_window", "window"),
        Binding("s", "cycle_sort", "sort"),
        Binding("j", "toggle_tables", "tables"),
        Binding("d", "toggle_models", "dbt models"),
        Binding("slash", "open_filter", "filter"),
        Binding("escape", "clear_filter", "clear", show=False),
        Binding("p", "toggle_pause", "pause"),
        Binding("question_mark", "help", "help"),
    ]

    def __init__(self, cfg: Config, source: Source) -> None:
        super().__init__()
        self.cfg = cfg
        self.source = source
        self.store = JobStore(
            source,
            cfg.pricing,
            tz=cfg.ui.timezone,
            max_hours=cfg.ui.max_window_hours,
            stream_rows=cfg.ui.stream_rows,
            top_n=cfg.ui.top_n,
            buckets=cfg.ui.timeline_buckets,
        )
        self.window_hours = cfg.ui.window_hours
        self.sort = "cost"
        self.filter = ""
        self.paused = False
        self.show_models = False
        self.snap: Snapshot | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        yield Sparkline([0.0], summary_function=max, id="timeline")
        yield Static("starting…", id="status")
        yield Input(
            placeholder="filter: principal, project, table, query text… (enter to apply, esc to cancel)", id="filter"
        )
        with Vertical():
            with Horizontal(id="top"):
                yield DataTable(id="principals", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="projects", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="bottom"):
                yield DataTable(id="tables", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        for tid, title in (
            ("principals", "principals"),
            ("projects", "projects"),
            ("tables", "hot tables"),
            ("jobs", "jobs"),
        ):
            self.query_one(f"#{tid}", DataTable).border_title = title
        self.set_interval(self.cfg.ui.refresh_seconds, self._tick)
        self.action_refresh_now()

    # ---- actions -------------------------------------------------------------------------------
    def _tick(self) -> None:
        if not self.paused:
            self.action_refresh_now()

    def action_refresh_now(self) -> None:
        self._status(Text("refreshing…", style="yellow"))
        self.load_snapshot()

    def action_cycle_window(self) -> None:
        i = WINDOWS.index(self.window_hours) if self.window_hours in WINDOWS else -1
        self.window_hours = WINDOWS[(i + 1) % len(WINDOWS)]
        self.action_refresh_now()  # may backfill; the store decides

    def action_cycle_sort(self) -> None:
        self.sort = SORT_KEYS[(SORT_KEYS.index(self.sort) + 1) % len(SORT_KEYS)]
        self._render_local()

    def action_toggle_tables(self) -> None:
        t = self.query_one("#tables", DataTable)
        t.display = not t.display

    def action_toggle_models(self) -> None:
        self.show_models = not self.show_models
        t = self.query_one("#tables", DataTable)
        t.display = True
        self._render_local()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.snap:
            self._render_local()

    def action_open_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.value = self.filter
        box.focus()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        if box.display:
            box.display = False
            self.set_focus(None)
            return
        if self.filter:
            self.filter = ""
            self._render_local()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self.filter = event.value.strip()
        event.input.display = False
        self.set_focus(self.query_one("#jobs", DataTable))
        self._render_local()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value if event.row_key else None
        if key is None:
            return
        tid = event.data_table.id
        if tid == "jobs":
            project_id, job_id = key.split("|", 1)
            job = self.store.get(project_id, job_id)
            if job:
                self.push_screen(JobDetail(job, self.source, self.cfg))
        else:
            self.filter = key
            self._render_local()

    # ---- data ----------------------------------------------------------------------------------
    @work(thread=True, exclusive=True, group="load")
    def load_snapshot(self) -> None:
        try:
            self.store.refresh(self.window_hours)
            snap = self.store.snapshot(self.window_hours, self.filter)
        except Exception as e:  # keep the last good screen, show the error
            self.call_from_thread(self._status, Text(f"error: {fmt.one_line(str(e), 300)}", style="bold red"))
            return
        self.call_from_thread(self._apply, snap)

    def _render_local(self) -> None:
        """Re-aggregate from the store without touching BigQuery (sort, filter, drill-down)."""
        if self.store.coverage_start is None:
            return
        self._apply(self.store.snapshot(self.window_hours, self.filter))

    def _apply(self, snap: Snapshot) -> None:
        self.snap = snap
        self.render_snapshot(snap)
        self._status(footer_text(snap, self.cfg, self.source.describe()))

    def _status(self, text: Text) -> None:
        self.query_one("#status", Static).update(text)

    # ---- rendering -----------------------------------------------------------------------------
    def render_snapshot(self, snap: Snapshot) -> None:
        cfg = self.cfg
        tz = ZoneInfo(cfg.ui.timezone)
        self.query_one("#summary", Static).update(summary_text(snap, cfg, self.source.describe(), self.paused))
        self.query_one("#timeline", Sparkline).data = snap.timeline or [0.0]

        t = self.query_one("#principals", DataTable)
        t.border_title = f"principals · by {self.sort}"
        _reset(t, ("principal", "jobs", "run", "err", "billed", "cost", "today", "slot-h", "budget"))
        for a in sort_aggs(snap.by_principal, self.sort)[: cfg.ui.top_n]:
            t.add_row(
                fmt.short_principal(a.key),
                _n(a.jobs),
                _n(a.running),
                err_cell(a.errors),
                fmt.bytes_(a.bytes_billed),
                fmt.money(a.cost),
                fmt.money(a.cost_today),
                fmt.slot_hours(a.slot_ms),
                cap_cell(a, cfg, "principal"),
                key=a.key,
            )

        t = self.query_one("#projects", DataTable)
        t.border_title = f"projects · by {self.sort}"
        _reset(t, ("project", "jobs", "run", "err", "billed", "cost", "today", "quota/budget"))
        for a in sort_aggs(snap.by_project, self.sort)[: cfg.ui.top_n]:
            t.add_row(
                a.key,
                _n(a.jobs),
                _n(a.running),
                err_cell(a.errors),
                fmt.bytes_(a.bytes_billed),
                fmt.money(a.cost),
                fmt.money(a.cost_today),
                cap_cell(a, cfg, "project"),
                key=a.key,
            )

        t = self.query_one("#tables", DataTable)
        if self.show_models:
            t.border_title = "dbt models · from the query comment (d: hot tables)"
            _reset(t, ("model", "runs", "err", "billed", "cost", "slot-h"))
            for a in snap.by_model[: cfg.ui.top_n]:
                t.add_row(
                    fmt.dbt_node(a.key),
                    _n(a.jobs),
                    err_cell(a.errors),
                    fmt.bytes_(a.bytes_billed),
                    fmt.money(a.cost),
                    fmt.slot_hours(a.slot_ms),
                    key=a.key,
                )
            if not snap.by_model:
                t.add_row("no dbt query comments in this window", "", "", "", "", "", key="")
        else:
            t.border_title = "hot tables (d: dbt models)"
            _reset(t, ("table", "jobs", "who", "billed", "cost"))
            for a in snap.by_table[: cfg.ui.top_n]:
                t.add_row(
                    a.key,
                    _n(a.jobs),
                    str(len(a.principals)),
                    fmt.bytes_(a.bytes_billed),
                    fmt.money(a.cost),
                    key=a.key,
                )

        t = self.query_one("#jobs", DataTable)
        t.border_title = (
            "jobs · running first, then newest"
            if self.source.has_running_jobs
            else "jobs · newest first (completed only)"
        )
        if snap.filter:
            t.border_title += f" · «{snap.filter}»"
        _reset(t, ("time", "state", "principal", "project", "type", "dur", "billed", "cost", "query"))
        qwidth = max(30, (t.size.width or 120) - 105)
        for j in snap.jobs[: cfg.ui.stream_rows]:
            text = j.error_message if j.error_code else (j.query or j.destination_table or "")
            t.add_row(
                fmt.clock(j.creation_time, tz),
                state_cell(j.state, bool(j.error_code)),
                fmt.short_principal(j.principal),
                j.project_id,
                fmt.stmt(j),
                fmt.duration(j.duration_s()),
                fmt.bytes_(j.bytes_billed),
                fmt.money(j.cost),
                fmt.one_line(text, qwidth),
                key=f"{j.project_id}|{j.job_id}",
            )


class JobDetail(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "close"), Binding("q", "dismiss", "close")]
    DEFAULT_CSS = """
    JobDetail { align: center middle; }
    #detail { width: 90%; height: 90%; border: thick $accent; background: $surface; padding: 1 2; }
    """

    def __init__(self, job: Job, source: Source, cfg: Config) -> None:
        super().__init__()
        self.job, self.source, self.cfg = job, source, cfg

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="detail"):
            yield Static(self._header(), id="detail_head")
            yield Static(Text(self.job.query or "(no query text)", style="cyan"), id="detail_query")

    def on_mount(self) -> None:
        if self.job.query and len(self.job.query) >= 300:
            self.fetch_full_query()

    def _header(self) -> Text:
        j, tz = self.job, ZoneInfo(self.cfg.ui.timezone)
        t = Text()
        t.append(f"{j.project_id} / {j.job_id}\n", style="bold")
        rows = [
            ("principal", j.principal),
            ("state", j.state),
            ("type", fmt.stmt(j)),
            ("created", j.creation_time.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")),
            ("duration", fmt.duration(j.duration_s())),
            ("bytes processed / billed", f"{fmt.bytes_(j.bytes_processed)} / {fmt.bytes_(j.bytes_billed)}"),
            ("cost", fmt.money(j.cost) + ("  (reservation " + j.reservation_id + ")" if j.reservation_id else "")),
            ("slot-h", fmt.slot_hours(j.slot_ms)),
            ("cache hit", str(j.cache_hit)),
            ("destination", j.destination_table or "-"),
            ("tables", "\n" + "\n".join(f"    {x}" for x in j.referenced_tables) if j.referenced_tables else "-"),
        ]
        if j.error_code:
            rows.append(("error", f"{j.error_code}: {j.error_message}"))
        for k, v in rows:
            t.append(f"{k:>26}  ", style="dim")
            t.append(f"{v}\n", style="red" if k == "error" else "")
        t.append("\nquery  ", style="dim")
        t.append("(esc to close)\n", style="dim")
        return t

    @work(thread=True)
    def fetch_full_query(self) -> None:
        try:
            full = self.source.fetch_query(self.job.project_id, self.job.job_id)
        except Exception as e:
            full = f"{self.job.query}\n\n[could not fetch the full text: {fmt.one_line(str(e), 200)}]"
        if full:
            self.app.call_from_thread(self.query_one("#detail_query", Static).update, Text(full, style="cyan"))


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("q", "dismiss", "close"),
        Binding("question_mark", "dismiss", "close"),
    ]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help { width: 96; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP, id="help")


def _reset(t: DataTable, cols: tuple[str, ...]) -> None:
    t.clear(columns=True)
    t.add_columns(*cols)


def _n(n: int) -> str:
    return f"{n:,}" if n else ""

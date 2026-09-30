from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_DBT_NODE = re.compile(r'"node_id"\s*:\s*"([^"]+)"')


@dataclass
class Job:
    creation_time: datetime
    project_id: str
    principal: str
    job_id: str
    state: str
    job_type: str | None = None
    statement_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    bytes_processed: int = 0
    bytes_billed: int = 0
    slot_ms: int = 0
    cache_hit: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    query: str | None = None
    destination_table: str | None = None
    referenced_tables: list[str] = field(default_factory=list)
    reservation_id: str | None = None
    cost: float = 0.0  # filled by Pricing

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_id, self.job_id)

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    @property
    def dbt_node(self) -> str | None:
        """dbt's default query comment carries the node id: /* {"app": "dbt", ..., "node_id": "model.x.y"} */"""
        if not self.query or "node_id" not in self.query[:600]:
            return None
        m = _DBT_NODE.search(self.query[:600])
        return m.group(1) if m else None

    def duration_s(self, now: datetime | None = None) -> float | None:
        if self.start_time is None:
            return None
        end = self.end_time or now or datetime.now(tz=self.start_time.tzinfo)
        return max(0.0, (end - self.start_time).total_seconds())

    def matches(self, needle: str) -> bool:
        n = needle.lower()
        return any(
            n in (s or "").lower()
            for s in (
                self.principal,
                self.project_id,
                self.query,
                self.statement_type,
                self.job_type,
                self.error_message,
                self.destination_table,
                *self.referenced_tables,
            )
        )


@dataclass
class Agg:
    """One aggregate row: a principal, a project or a table."""

    key: str
    jobs: int = 0
    running: int = 0
    errors: int = 0
    cache_hits: int = 0
    bytes_processed: int = 0
    bytes_billed: int = 0
    slot_ms: int = 0
    cost: float = 0.0
    bytes_billed_today: int = 0
    cost_today: float = 0.0
    principals: set[str] = field(default_factory=set)

    def add(self, j: Job, in_window: bool, in_today: bool) -> None:
        if in_window:
            self.jobs += 1
            self.running += j.state != "DONE"
            self.errors += j.error_code is not None
            self.cache_hits += bool(j.cache_hit)
            self.bytes_processed += j.bytes_processed
            self.bytes_billed += j.bytes_billed
            self.slot_ms += j.slot_ms
            self.cost += j.cost
            self.principals.add(j.principal)
        if in_today:
            self.bytes_billed_today += j.bytes_billed
            self.cost_today += j.cost


@dataclass
class Snapshot:
    fetched_at: datetime
    window_hours: float
    filter: str = ""
    by_principal: list[Agg] = field(default_factory=list)
    by_project: list[Agg] = field(default_factory=list)
    by_table: list[Agg] = field(default_factory=list)
    by_model: list[Agg] = field(default_factory=list)  # dbt nodes parsed from the query comment
    jobs: list[Job] = field(default_factory=list)
    timeline: list[float] = field(default_factory=list)  # cost per bucket, oldest first
    bucket_minutes: float = 0.0
    store_rows: int = 0
    coverage_start: datetime | None = None
    refresh_bytes_billed: int = 0  # bqtop's own spend, this refresh
    session_bytes_billed: int = 0  # bqtop's own spend, since start
    refresh_seconds: float = 0.0

    @property
    def totals(self) -> Agg:
        t = Agg(key="total")
        for a in self.by_project:
            t.jobs += a.jobs
            t.running += a.running
            t.errors += a.errors
            t.cache_hits += a.cache_hits
            t.bytes_processed += a.bytes_processed
            t.bytes_billed += a.bytes_billed
            t.slot_ms += a.slot_ms
            t.cost += a.cost
            t.bytes_billed_today += a.bytes_billed_today
            t.cost_today += a.cost_today
        t.principals = {a.key for a in self.by_principal}
        return t

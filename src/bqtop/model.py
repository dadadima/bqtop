from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Agg:
    """One aggregate row (a principal, a project or a table)."""

    key: str
    jobs: int = 0
    running: int = 0
    errors: int = 0
    bytes_processed: int = 0
    bytes_billed: int = 0
    slot_ms: int = 0
    bytes_billed_today: int = 0
    principals: int = 0

    def cost(self, price_per_tib: float) -> float:
        return self.bytes_billed / 2**40 * price_per_tib

    def cost_today(self, price_per_tib: float) -> float:
        return self.bytes_billed_today / 2**40 * price_per_tib


@dataclass
class Job:
    creation_time: datetime
    project_id: str
    principal: str
    job_id: str
    state: str
    job_type: str | None
    statement_type: str | None
    start_time: datetime | None
    end_time: datetime | None
    bytes_processed: int
    bytes_billed: int
    slot_ms: int
    cache_hit: bool | None
    error_code: str | None
    error_message: str | None
    query: str | None
    destination_table: str | None
    n_tables: int

    def cost(self, price_per_tib: float) -> float:
        return self.bytes_billed / 2**40 * price_per_tib

    def duration_s(self) -> float | None:
        if self.start_time is None:
            return None
        end = self.end_time or datetime.now(tz=self.start_time.tzinfo)
        return (end - self.start_time).total_seconds()


@dataclass
class Snapshot:
    fetched_at: datetime
    window_hours: float
    by_principal: list[Agg] = field(default_factory=list)
    by_project: list[Agg] = field(default_factory=list)
    by_table: list[Agg] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    query_bytes_billed: int = 0  # what bqtop itself consumed for this refresh

    @property
    def totals(self) -> Agg:
        t = Agg(key="total")
        for a in self.by_project:
            t.jobs += a.jobs
            t.running += a.running
            t.errors += a.errors
            t.bytes_processed += a.bytes_processed
            t.bytes_billed += a.bytes_billed
            t.slot_ms += a.slot_ms
            t.bytes_billed_today += a.bytes_billed_today
        t.principals = len(self.by_principal)
        return t

"""In-memory job store. Backfilled once for the widest window asked for, then kept current with
incremental fetches (new jobs + jobs that were still running). Aggregation happens here, in Python,
so sorting, filtering and window changes never touch BigQuery."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bqtop.model import Agg, Job, Snapshot
from bqtop.pricing import Pricing
from bqtop.sources.base import Source

OVERLAP = timedelta(minutes=2)  # re-read this much before the newest job seen, in case of late rows


class JobStore:
    def __init__(
        self,
        source: Source,
        pricing: Pricing,
        tz: str = "UTC",
        max_hours: float = 168,
        stream_rows: int = 40,
        top_n: int = 15,
        buckets: int = 48,
    ) -> None:
        self.source = source
        self.pricing = pricing
        self.tz = ZoneInfo(tz)
        self.max_hours = max_hours
        self.stream_rows = stream_rows
        self.top_n = top_n
        self.buckets = buckets
        self.jobs: dict[tuple[str, str], Job] = {}
        self.coverage_start: datetime | None = None
        self.newest: datetime | None = None
        self.session_bytes_billed = 0
        self.last_refresh_bytes = 0
        self.last_refresh_seconds = 0.0
        self._lock = threading.Lock()

    # ---- loading -------------------------------------------------------------------------------
    def refresh(self, window_hours: float) -> None:
        """Make sure [window start, now] and [local midnight, now] are covered, then pull the delta."""
        t0 = time.monotonic()
        now = datetime.now(tz=UTC)
        need_start = min(now - timedelta(hours=window_hours), self._day_start(now))
        billed = 0

        if self.coverage_start is None:
            billed += self._load(need_start, now, pending=[])
            self.coverage_start = need_start
        else:
            if need_start < self.coverage_start:
                billed += self._load(need_start, self.coverage_start, pending=[])
                self.coverage_start = need_start
            since = (self.newest or self.coverage_start) - OVERLAP
            billed += self._load(since, now, pending=self._pending_ids())

        self._prune(now)
        self.last_refresh_bytes = billed
        self.session_bytes_billed += billed
        self.last_refresh_seconds = time.monotonic() - t0

    def _load(self, since: datetime, until: datetime, pending: list[str]) -> int:
        rows, billed = self.source.fetch_jobs(since, until, pending)
        with self._lock:
            for j in rows:
                j.cost = self.pricing.cost(j)
                self.jobs[j.key] = j
                if self.newest is None or j.creation_time > self.newest:
                    self.newest = j.creation_time
        return billed

    def _pending_ids(self) -> list[str]:
        if not self.source.has_running_jobs:
            return []
        return [j.job_id for j in self.jobs.values() if not j.done]

    def _prune(self, now: datetime) -> None:
        floor = now - timedelta(hours=self.max_hours)
        with self._lock:
            for k in [k for k, j in self.jobs.items() if j.creation_time < floor]:
                del self.jobs[k]
        if self.coverage_start and self.coverage_start < floor:
            self.coverage_start = floor

    def _day_start(self, now: datetime) -> datetime:
        local = now.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return local.astimezone(UTC)

    # ---- aggregation ---------------------------------------------------------------------------
    def snapshot(self, window_hours: float, filter: str = "") -> Snapshot:
        now = datetime.now(tz=UTC)
        win_start = now - timedelta(hours=window_hours)
        day_start = self._day_start(now)
        snap = Snapshot(
            fetched_at=now,
            window_hours=window_hours,
            filter=filter,
            store_rows=len(self.jobs),
            coverage_start=self.coverage_start,
            refresh_bytes_billed=self.last_refresh_bytes,
            session_bytes_billed=self.session_bytes_billed,
            refresh_seconds=self.last_refresh_seconds,
        )
        by_p: dict[str, Agg] = {}
        by_j: dict[str, Agg] = {}
        by_t: dict[str, Agg] = {}
        timeline = [0.0] * self.buckets
        bucket = timedelta(hours=window_hours) / self.buckets
        snap.bucket_minutes = bucket.total_seconds() / 60
        stream: list[Job] = []

        with self._lock:
            jobs = list(self.jobs.values())
        for j in jobs:
            if filter and not j.matches(filter):
                continue
            in_win = j.creation_time >= win_start
            in_today = j.creation_time >= day_start
            if not (in_win or in_today):
                continue
            by_p.setdefault(j.principal, Agg(j.principal)).add(j, in_win, in_today)
            by_j.setdefault(j.project_id, Agg(j.project_id)).add(j, in_win, in_today)
            if in_win:
                for t in j.referenced_tables:
                    by_t.setdefault(t, Agg(t)).add(j, True, False)
                idx = min(self.buckets - 1, int((j.creation_time - win_start) / bucket))
                timeline[idx] += j.cost
                stream.append(j)

        snap.by_principal = sorted(
            (a for a in by_p.values() if a.jobs or a.cost_today),
            key=lambda a: (a.cost, a.bytes_billed, a.jobs),
            reverse=True,
        )
        snap.by_project = sorted(
            (a for a in by_j.values() if a.jobs or a.cost_today),
            key=lambda a: (a.cost, a.bytes_billed, a.jobs),
            reverse=True,
        )
        snap.by_table = sorted(by_t.values(), key=lambda a: (a.cost, a.bytes_billed, a.jobs), reverse=True)[
            : self.top_n
        ]
        stream.sort(key=lambda j: (j.state != "DONE", j.creation_time), reverse=True)
        snap.jobs = stream[: self.stream_rows]
        snap.timeline = timeline
        return snap

    def get(self, project_id: str, job_id: str) -> Job | None:
        return self.jobs.get((project_id, job_id))

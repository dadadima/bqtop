"""Shared fetch logic. A source only has to provide `base_sql()`: a SELECT that yields one row per
job with the normalized columns below. Everything else (aggregation, the stream, concurrency) is
common.

Normalized columns:
  creation_time TIMESTAMP, project_id STRING, principal STRING, job_id STRING, job_type STRING,
  statement_type STRING, state STRING, start_time TIMESTAMP, end_time TIMESTAMP,
  bytes_processed INT64, bytes_billed INT64, slot_ms INT64, cache_hit BOOL,
  error_code STRING, error_message STRING, query STRING,
  referenced_tables ARRAY<STRING>, destination_table STRING
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from bqtop.config import Config
from bqtop.model import Agg, Job, Snapshot

# One script job: the base scan lands in a temp table once, the three aggregations read that.
_SCRIPT_SQL = """
create temp table jobs as
{base};

-- agg
select grp, key,
    countif(creation_time >= @win) as jobs,
    countif(creation_time >= @win and state = 'RUNNING') as running,
    countif(creation_time >= @win and error_code is not null) as errors,
    sum(if(creation_time >= @win, coalesce(bytes_processed, 0), 0)) as bytes_processed,
    sum(if(creation_time >= @win, coalesce(bytes_billed, 0), 0)) as bytes_billed,
    sum(if(creation_time >= @win, coalesce(slot_ms, 0), 0)) as slot_ms,
    sum(if(creation_time >= @day_start, coalesce(bytes_billed, 0), 0)) as bytes_billed_today
from jobs
cross join unnest([
    struct('principal' as grp, coalesce(principal, '(unknown)') as key),
    struct('project' as grp, coalesce(project_id, '(unknown)') as key)
]) as g
group by grp, key
having jobs > 0 or bytes_billed_today > 0;

-- tables
select t as key,
    count(*) as jobs,
    countif(error_code is not null) as errors,
    sum(coalesce(bytes_processed, 0)) as bytes_processed,
    sum(coalesce(bytes_billed, 0)) as bytes_billed,
    sum(coalesce(slot_ms, 0)) as slot_ms,
    count(distinct principal) as principals
from jobs, unnest(referenced_tables) as t
where creation_time >= @win
group by t
order by bytes_billed desc, jobs desc
limit {top_n};

-- stream
select * except (referenced_tables, query),
    substr(query, 1, 400) as query,
    array_length(referenced_tables) as n_tables
from jobs
where creation_time >= @win
order by (state = 'RUNNING') desc, creation_time desc
limit {stream_rows};
"""


class Source(ABC):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client: bigquery.Client | None = None

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=self.cfg.source.billing_project)
        return self._client

    @abstractmethod
    def base_sql(self) -> str:
        """SELECT producing the normalized job rows, filtered on `creation_time >= @window_start`."""

    @abstractmethod
    def describe(self) -> str:
        """One-line description for the header."""

    @property
    def has_running_jobs(self) -> bool:
        return True

    def fetch(self, window_hours: float | None = None) -> Snapshot:
        ui = self.cfg.ui
        window_hours = window_hours or ui.window_hours
        tz = ZoneInfo(ui.timezone)
        now = datetime.now(tz=timezone.utc)
        window_start = now - timedelta(hours=window_hours)
        local_midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = local_midnight.astimezone(timezone.utc)
        # the day rollup needs data since midnight even when the window is shorter than that
        base_start = min(window_start, day_start)

        params = [
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", base_start),
            bigquery.ScalarQueryParameter("day_start", "TIMESTAMP", day_start),
            bigquery.ScalarQueryParameter("win", "TIMESTAMP", window_start),
        ]
        sql = _SCRIPT_SQL.format(base=self.base_sql(), top_n=int(ui.top_n), stream_rows=int(ui.stream_rows))
        results, billed = self._run_script(sql, params)

        snap = Snapshot(fetched_at=now, window_hours=window_hours)
        snap.query_bytes_billed = billed

        for r in results["agg"]:
            a = Agg(
                key=r["key"], jobs=r["jobs"], running=r["running"], errors=r["errors"],
                bytes_processed=r["bytes_processed"], bytes_billed=r["bytes_billed"],
                slot_ms=r["slot_ms"], bytes_billed_today=r["bytes_billed_today"],
            )
            (snap.by_principal if r["grp"] == "principal" else snap.by_project).append(a)

        for r in results["tables"]:
            snap.by_table.append(Agg(
                key=r["key"], jobs=r["jobs"], errors=r["errors"], bytes_processed=r["bytes_processed"],
                bytes_billed=r["bytes_billed"], slot_ms=r["slot_ms"], principals=r["principals"],
            ))

        for r in results["stream"]:
            snap.jobs.append(Job(
                creation_time=r["creation_time"], project_id=r["project_id"] or "-",
                principal=r["principal"] or "-", job_id=r["job_id"] or "-", state=r["state"] or "-",
                job_type=r["job_type"], statement_type=r["statement_type"],
                start_time=r["start_time"], end_time=r["end_time"],
                bytes_processed=r["bytes_processed"] or 0, bytes_billed=r["bytes_billed"] or 0,
                slot_ms=r["slot_ms"] or 0, cache_hit=r["cache_hit"], error_code=r["error_code"],
                error_message=r["error_message"], query=r["query"],
                destination_table=r["destination_table"], n_tables=r["n_tables"] or 0,
            ))

        snap.by_principal.sort(key=lambda a: (a.bytes_billed, a.jobs), reverse=True)
        snap.by_project.sort(key=lambda a: (a.bytes_billed, a.jobs), reverse=True)
        return snap

    def _run_script(self, sql: str, params: list) -> tuple[dict[str, list[dict]], int]:
        """Run the multi-statement script and collect the three result sets from its child jobs.
        Children are matched by result schema, not by order."""
        parent = self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
        parent.result()
        out: dict[str, list[dict]] = {"agg": [], "tables": [], "stream": []}
        billed = 0
        for child in self.client.list_jobs(parent_job=parent.job_id):
            billed += child.total_bytes_billed or 0
            if child.statement_type != "SELECT":
                continue
            names = {f.name for f in child.result().schema}
            key = "agg" if "grp" in names else "tables" if "principals" in names else "stream"
            out[key] = [dict(r.items()) for r in child.result()]
        return out, billed

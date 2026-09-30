"""A source turns BigQuery job metadata into normalized `Job` rows. Two methods matter:

  fetch_jobs(since, until, pending_ids) -> (jobs, bytes_billed)
      jobs created in [since, until) plus any job whose id is in `pending_ids` (to pick up state
      changes of jobs that were still running), and what the fetch itself billed.
  fetch_query(project_id, job_id) -> full query text, or None if the source cannot provide it.

Concrete sources implement `base_sql()` returning a SELECT with the normalized columns and a `{where}`
placeholder; the base class does parameters, execution and row mapping.

Normalized columns:
  creation_time TIMESTAMP, project_id STRING, principal STRING, job_id STRING, job_type STRING,
  statement_type STRING, state STRING, start_time TIMESTAMP, end_time TIMESTAMP,
  bytes_processed INT64, bytes_billed INT64, slot_ms INT64, cache_hit BOOL, error_code STRING,
  error_message STRING, query STRING, referenced_tables ARRAY<STRING>, destination_table STRING,
  reservation_id STRING
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from google.cloud import bigquery

from bqtop.config import Config
from bqtop.model import Job

QUERY_SNIPPET_CHARS = 300


class Source(ABC):
    has_running_jobs = True  # False when the source only sees finished jobs

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
        """SELECT of normalized columns with a `{where}` placeholder (no trailing semicolon)."""

    @abstractmethod
    def time_where(self) -> str:
        """WHERE clause using @since, @until, @floor, @pending (ARRAY<STRING>) parameters."""

    @abstractmethod
    def describe(self) -> str:
        """One-line description for the header."""

    def query_sql(self) -> str | None:
        """SELECT query FROM ... WHERE project/job match (@project_id, @job_id), or None."""
        return None

    # ---- public API ------------------------------------------------------------------------------
    def fetch_jobs(self, since: datetime, until: datetime, pending_ids: list[str]) -> tuple[list[Job], int]:
        sql = self.base_sql().format(where=self.time_where())
        params = [
            bigquery.ScalarQueryParameter("since", "TIMESTAMP", since),
            bigquery.ScalarQueryParameter("until", "TIMESTAMP", until),
            # pending jobs are at most max_hours old; a floor keeps partition pruning effective
            bigquery.ScalarQueryParameter("floor", "TIMESTAMP", since if not pending_ids else _floor(since)),
            bigquery.ArrayQueryParameter("pending", "STRING", pending_ids[:5000]),
        ]
        job = self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
        rows = [self._to_job(r) for r in job.result()]
        return rows, job.total_bytes_billed or 0

    def fetch_query(self, project_id: str, job_id: str) -> str | None:
        sql = self.query_sql()
        if sql is None:
            return None
        params = [
            bigquery.ScalarQueryParameter("project_id", "STRING", project_id),
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
        ]
        rows = list(self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
        return rows[0]["query"] if rows else None

    def probe(self) -> list[tuple[str, bool, str]]:
        """Connectivity checks for `bqtop --check`: (what, ok, detail)."""
        out = []
        try:
            who = list(self.client.query("select session_user() as who").result())[0]["who"]
            out.append(("identity", True, f"{who} (jobs run in {self.cfg.source.billing_project})"))
        except Exception as e:
            out.append(("identity", False, _short(e)))
            return out
        try:
            sql = self.base_sql().format(where=self.time_where())
            cfg = bigquery.QueryJobConfig(
                dry_run=True,
                query_parameters=[
                    bigquery.ScalarQueryParameter("since", "TIMESTAMP", datetime.now().astimezone() - _DAY),
                    bigquery.ScalarQueryParameter("until", "TIMESTAMP", datetime.now().astimezone()),
                    bigquery.ScalarQueryParameter("floor", "TIMESTAMP", datetime.now().astimezone() - _DAY),
                    bigquery.ArrayQueryParameter("pending", "STRING", []),
                ],
            )
            j = self.client.query(sql, job_config=cfg)
            out.append(
                ("source", True, f"{self.describe()} · a 24h backfill scans ~{j.total_bytes_processed / 2**20:.0f} MiB")
            )
        except Exception as e:
            out.append(("source", False, _short(e)))
        return out

    # ---- helpers -----------------------------------------------------------------------------
    @staticmethod
    def _to_job(r) -> Job:
        return Job(
            creation_time=r["creation_time"],
            project_id=r["project_id"] or "(unknown)",
            principal=r["principal"] or "(unknown)",
            job_id=r["job_id"] or "-",
            state=r["state"] or "DONE",
            job_type=r["job_type"],
            statement_type=r["statement_type"],
            start_time=r["start_time"],
            end_time=r["end_time"],
            bytes_processed=r["bytes_processed"] or 0,
            bytes_billed=r["bytes_billed"] or 0,
            slot_ms=r["slot_ms"] or 0,
            cache_hit=r["cache_hit"],
            error_code=r["error_code"],
            error_message=r["error_message"],
            query=r["query"],
            destination_table=r["destination_table"],
            referenced_tables=list(r["referenced_tables"] or []),
            reservation_id=r["reservation_id"],
        )


_DAY = timedelta(hours=24)


def _floor(since: datetime) -> datetime:
    return since - timedelta(hours=48)


def _short(e: Exception) -> str:
    return " ".join(str(e).split())[:300]

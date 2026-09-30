"""Synthetic jobs so `bqtop --demo` works without a GCP project. Deterministic-ish, refreshes add
a few new jobs and finish the running ones."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from bqtop.model import Job
from bqtop.sources.base import Source

_PRINCIPALS = [
    ("svc-airflow-prod@acme-ingest-prod.iam.gserviceaccount.com", "acme-std-prod", 0.55),
    ("svc-dbt-ci@acme-ingest-prod.iam.gserviceaccount.com", "acme-anl-dev", 0.15),
    ("svc-agent-reader@acme-agents-prod.iam.gserviceaccount.com", "acme-agents-prod", 0.10),
    ("svc-fivetran@acme-ingest-prod.iam.gserviceaccount.com", "acme-ingest-prod", 0.10),
    ("ada@acme.example", "acme-anl-prod", 0.05),
    ("grace@acme.example", "acme-anl-prod", 0.03),
    ("linus@acme.example", "acme-agents-prod", 0.02),
]
_TABLES = [
    "acme-std-prod.std_crm.accounts",
    "acme-std-prod.std_crm.opportunities",
    "acme-std-prod.std_product.events",
    "acme-std-prod.std_product.daily_activity",
    "acme-anl-prod.data_model.dim_customer",
    "acme-anl-prod.data_model.fact_usage",
    "acme-ingest-prod.stg_crm.accounts_raw",
    "acme-std-prod.std_hr.headcount",
]
_QUERIES = [
    "select customer_id, sum(amount) from `acme-std-prod.std_crm.opportunities` where close_date >= '2026-01-01' group by 1",
    '/* {"app": "dbt", "node_id": "model.analytics.fact_usage"} */ create or replace table `acme-anl-prod.data_model.fact_usage` as select * from ...',
    "merge into `acme-std-prod.std_product.daily_activity` t using stg s on t.id = s.id when matched then update set ...",
    "select count(*) from `acme-anl-prod.data_model.dim_customer`",
    "with events as (select * from `acme-std-prod.std_product.events` where date_partition = current_date()) select account_id, count(*) from events group by 1",
    "select * from `acme-std-prod.std_hr.headcount`",
]
_ERRORS = [
    ("invalidQuery", "Unrecognized name: customer_key at [3:12]"),
    ("notFound", "Not found: Table acme-anl-dev:pr42_data_model.dim_customer"),
    ("quotaExceeded", "Custom quota exceeded: Your usage exceeded the custom quota for QueryUsagePerDay"),
]


class DemoSource(Source):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.rng = random.Random(42)
        self._n = 0

    @property
    def client(self):  # never touches GCP
        raise RuntimeError("demo source has no BigQuery client")

    def base_sql(self) -> str:
        return ""

    def time_where(self) -> str:
        return ""

    def describe(self) -> str:
        return "demo data (synthetic)"

    def probe(self):
        return [("identity", True, "demo"), ("source", True, "synthetic jobs")]

    def fetch_query(self, project_id: str, job_id: str) -> str | None:
        return _QUERIES[int(job_id.split("_")[-1]) % len(_QUERIES)] * 3

    def fetch_jobs(self, since: datetime, until: datetime, pending_ids: list[str]) -> tuple[list[Job], int]:
        hours = max(0.05, (until - since).total_seconds() / 3600)
        n = int(hours * 60) if self.coverage_initial(since, until) else self.rng.randint(1, 6)
        jobs = [self._job(since, until) for _ in range(n)]
        for pid in pending_ids:  # finish previously running jobs
            j = self._job(until - timedelta(minutes=3), until)
            j.job_id, j.state, j.end_time = pid, "DONE", until
            jobs.append(j)
        return jobs, 10 * 2**20

    def coverage_initial(self, since: datetime, until: datetime) -> bool:
        return (until - since) > timedelta(minutes=10)

    def _job(self, since: datetime, until: datetime) -> Job:
        r = self.rng
        principal, project, _ = r.choices(_PRINCIPALS, weights=[w for _, _, w in _PRINCIPALS])[0]
        created = since + (until - since) * r.random()
        for _ in range(3):  # nights are quieter: resample small-hours jobs
            if created.astimezone(UTC).hour in range(1, 6) and r.random() < 0.6:
                created = since + (until - since) * r.random()
        self._n += 1
        gb = r.lognormvariate(1.5, 2.0)  # heavy tail
        billed = int(min(gb, 4000) * 1e9) // (10 * 2**20) * (10 * 2**20) if r.random() > 0.15 else 0
        running = (until - created) < timedelta(minutes=4) and r.random() < 0.5
        err = r.random() < 0.04 and not running
        code, msg = r.choice(_ERRORS) if err else (None, None)
        qi = r.randrange(len(_QUERIES))
        stmt = ["SELECT", "CREATE_TABLE_AS_SELECT", "MERGE", "SELECT", "SELECT", "SELECT"][qi]
        dur = timedelta(seconds=r.lognormvariate(2.5, 1.0))
        return Job(
            creation_time=created,
            project_id=project,
            principal=principal,
            job_id=f"job_{self._n:07d}",
            state="RUNNING" if running else "DONE",
            job_type="QUERY",
            statement_type=stmt,
            start_time=created + timedelta(seconds=1),
            end_time=None if running else created + dur,
            bytes_processed=int(billed * r.uniform(0.6, 1.0)),
            bytes_billed=0 if err else billed,
            slot_ms=int(dur.total_seconds() * 1000 * r.uniform(2, 60)),
            cache_hit=(billed == 0 and not err),
            error_code=code,
            error_message=msg,
            query=_QUERIES[qi],
            destination_table="acme-anl-prod.data_model.fact_usage" if stmt != "SELECT" else None,
            referenced_tables=r.sample(_TABLES, r.randint(1, 3)),
            reservation_id="acme-admin:us.dbt-reservation" if project == "acme-std-prod" and r.random() < 0.5 else None,
        )

import copy
from datetime import UTC, datetime, timedelta

from bqtop.config import Config
from bqtop.model import Job
from bqtop.pricing import Pricing, Rate
from bqtop.store import JobStore

NOW = datetime.now(tz=UTC)


class FakeSource:
    has_running_jobs = True

    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = []

    def fetch_jobs(self, since, until, pending_ids):
        self.calls.append((since, until, list(pending_ids)))
        rows = [copy.copy(j) for j in self.jobs if since <= j.creation_time < until or j.job_id in pending_ids]
        return rows, 10 * 2**20

    def describe(self):
        return "fake"


def job(
    minutes_ago,
    principal="a@x",
    project="p1",
    billed=2**40,
    tables=(),
    state="DONE",
    err=None,
    reservation=None,
    slot_ms=0,
    jid=None,
):
    t = NOW - timedelta(minutes=minutes_ago)
    return Job(
        creation_time=t,
        project_id=project,
        principal=principal,
        job_id=jid or f"j{minutes_ago}",
        state=state,
        statement_type="SELECT",
        start_time=t,
        end_time=None if state != "DONE" else t,
        bytes_processed=billed,
        bytes_billed=billed,
        slot_ms=slot_ms,
        error_code=err,
        referenced_tables=list(tables),
        reservation_id=reservation,
    )


def test_aggregation_window_and_today():
    src = FakeSource(
        [
            job(10, "a@x", "p1", 2**40, tables=["t1", "t2"]),
            job(30, "b@x", "p2", 2**39, tables=["t1"], err="invalidQuery"),
            job(200, "a@x", "p1", 2**40, state="RUNNING", jid="run1"),
        ]
    )
    store = JobStore(
        src, Pricing(default=Rate(mode="on_demand", on_demand_usd_per_tib=6.25)), tz="UTC", stream_rows=10, top_n=5
    )
    store.refresh(window_hours=1)
    snap = store.snapshot(window_hours=1)
    t = snap.totals
    assert t.jobs == 2 and t.errors == 1 and t.running == 0
    assert abs(t.cost - 6.25 * 1.5) < 1e-6
    assert [a.key for a in snap.by_principal] == ["a@x", "b@x"]
    assert snap.by_table[0].key == "t1" and snap.by_table[0].jobs == 2
    assert len(snap.timeline) == 48 and sum(snap.timeline) > 0
    # the running job is 200 min old: outside the 1h window, but visible in a 6h one
    snap6 = store.snapshot(window_hours=6)
    assert snap6.totals.running == (1 if (NOW - timedelta(minutes=200)) >= store.coverage_start else 0)


def test_incremental_refresh_refetches_pending():
    running = job(3, state="RUNNING", jid="r1")
    src = FakeSource([job(10), running])
    store = JobStore(src, Pricing(), tz="UTC")
    store.refresh(1)
    assert store.snapshot(1).totals.running == 1
    running.state = "DONE"
    store.refresh(1)
    assert "r1" in src.calls[-1][2]  # pending id was asked for
    assert store.snapshot(1).totals.running == 0
    assert len(store.jobs) == 2  # upsert, no duplicates


def test_widening_window_backfills_once():
    src = FakeSource([job(30), job(5 * 60), job(30 * 60)])
    store = JobStore(src, Pricing(), tz="UTC")
    store.refresh(1)
    assert store.snapshot(1).totals.jobs == 1
    store.refresh(72)
    assert store.snapshot(72).totals.jobs == 3
    first_backfill = [c for c in src.calls if c[1] <= store.coverage_start + timedelta(hours=72)]
    assert first_backfill  # an older range was requested exactly once
    store.refresh(72)
    assert store.snapshot(72).totals.jobs == 3


def test_filter_and_pricing_auto():
    pr = Pricing(default=Rate(mode="auto", on_demand_usd_per_tib=6.25, slot_usd_per_hour=0.06))
    src = FakeSource(
        [
            job(5, "a@x", "p1", billed=2**40),
            job(6, "b@x", "p2", billed=2**40, reservation="res", slot_ms=3_600_000 * 10),
        ]
    )
    store = JobStore(src, pr, tz="UTC")
    store.refresh(1)
    snap = store.snapshot(1)
    costs = {a.key: a.cost for a in snap.by_principal}
    assert abs(costs["a@x"] - 6.25) < 1e-6
    assert abs(costs["b@x"] - 0.6) < 1e-6  # 10 slot-hours × 0.06, not bytes
    only_b = store.snapshot(1, filter="p2")
    assert [a.key for a in only_b.by_principal] == ["b@x"]


def test_demo_source_runs_end_to_end():
    from bqtop.sources import make_source

    cfg = Config.demo()
    store = JobStore(make_source(cfg), cfg.pricing, tz="UTC", stream_rows=5, top_n=5)
    store.refresh(6)
    snap = store.snapshot(6)
    assert snap.totals.jobs > 50 and snap.jobs and snap.by_table


def test_dbt_models_from_query_comment():
    q = (
        '/* {"app": "dbt", "dbt_version": "1.9.0", "profile_name": "x", '
        '"node_id": "model.analytics.fact_usage"} */\nselect 1'
    )
    j1 = job(5, "svc@x", "p1", billed=2**40)
    j1.query = q
    j2 = job(6, "svc@x", "p1", billed=2**39)
    j2.query = q
    j3 = job(7, "ada@x", "p1", billed=2**40)  # no dbt comment
    assert j1.dbt_node == "model.analytics.fact_usage" and j3.dbt_node is None
    store = JobStore(FakeSource([j1, j2, j3]), Pricing(), tz="UTC")
    store.refresh(1)
    snap = store.snapshot(1)
    assert [a.key for a in snap.by_model] == ["model.analytics.fact_usage"]
    assert snap.by_model[0].jobs == 2 and snap.by_model[0].bytes_billed == 2**40 + 2**39

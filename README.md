# bqtop

htop for BigQuery. A terminal dashboard that shows, for the last N hours, who is running what on your
BigQuery projects, what it costs, which tables are hot, and which jobs are running right now.

```
 bqtop ─ window 24h  jobs 12,431  running 3  errors 7 | billed 4.2 TiB = $26.25 | today $9.10 | slot-h 118.4 ...
 ┌ principals · by cost ─────────────────┐ ┌ projects · by cost ──────────────────────────────────┐
 │ principal            jobs  err billed │ │ project              jobs  billed   cost  today quota│
 │ svc-airflow-prod@…  8,102    2 3.1 TiB│ │ my-std-prod         9,876 3.5 TiB $21.9  $7.4  7% of│
 │ svc-agent@…           451    0 480 GiB│ │ my-agents-prod        451 480 GiB  $2.9  $2.9 58% of│
 └───────────────────────────────────────┘ └──────────────────────────────────────────────────────┘
 ┌ hot tables ─────────────┐ ┌ jobs · running first, then newest ─────────────────────────────────┐
 │ table      jobs billed  │ │ 14:02:11 RUNNING svc-agent@…  my-agents-prod select 3.1m 67 GiB … │
 └─────────────────────────┘ └────────────────────────────────────────────────────────────────────┘
```

No agents, no sinks to build, no dashboards to click through: it reads the metadata BigQuery already
keeps and refreshes on an interval, like `htop`.

## Install

```bash
uv tool install git+https://github.com/dadadima/bqtop      # or: pipx install git+https://github.com/dadadima/bqtop
bqtop --init                                                # writes ~/.config/bqtop/config.toml
$EDITOR ~/.config/bqtop/config.toml
bqtop --once                                                # one snapshot, checks the connection
bqtop                                                       # the TUI
```

Authentication is whatever `google-cloud-bigquery` finds: Application Default Credentials
(`gcloud auth application-default login`), a service-account key via `GOOGLE_APPLICATION_CREDENTIALS`,
or the metadata server.

## Sources

bqtop reads one of two standard places. Pick with `[source].kind`.

| kind                 | Reads                                             | Running jobs | Permissions                                                        |
| -------------------- | ------------------------------------------------- | ------------ | ------------------------------------------------------------------ |
| `information_schema` | `INFORMATION_SCHEMA.JOBS_BY_{PROJECT,FOLDER,ORGANIZATION,USER}` | yes | `bigquery.jobs.listAll` at that level (e.g. `roles/bigquery.resourceViewer`); none for `user` |
| `audit_log`          | a routed `cloudaudit_googleapis_com_data_access` table | no (DONE events only) | `dataViewer` on the sink table, `jobUser` on the billing project |

`information_schema` is the default and the most direct: it is real time, includes `RUNNING` jobs and
needs nothing set up. `scope = "folder"` gives one view over every project under the folder that
contains `billing_project`. `audit_log` is for setups that already route BigQuery audit logs to a
table (a folder- or org-level sink), which also works when you cannot get `jobs.listAll` on every
project.

## Configuration

`~/.config/bqtop/config.toml` (or `./bqtop.toml`). Full example in [`examples/config.example.toml`](examples/config.example.toml).

```toml
[source]
kind = "information_schema"
billing_project = "my-admin-project"   # runs and pays for bqtop's own queries
region = "us"
scope = "folder"                       # project | folder | organization | user

[pricing]
on_demand_usd_per_tib = 6.25           # cost column = bytes billed × this

[quotas]                               # optional: daily QueryUsagePerDay caps, shown as % used today
"my-agents-project" = "5 TiB"

[ui]
refresh_seconds = 60
window_hours = 24
timezone = "Europe/Brussels"
top_n = 15
stream_rows = 40
```

## Keys

| key | action                                            |
| --- | ------------------------------------------------- |
| `q` | quit                                              |
| `r` | refresh now                                       |
| `w` | cycle window: 1h, 6h, 24h, 72h, 168h              |
| `s` | cycle sort: cost, bytes, jobs, errors, slots      |
| `j` | hide the hot-tables panel to widen the job stream |

## Scripting

```bash
bqtop --once                # Rich tables
bqtop --once --json         # the same snapshot as JSON
bqtop --once -w 6 --sort errors
```

## What the numbers mean

- **billed / cost**: `total_bytes_billed` × the on-demand price. On reservations (Editions) the bytes
  are still reported but you pay for slots; look at `slot-h` instead.
- **today**: bytes billed since local midnight in `[ui].timezone`, regardless of the window. This is
  what the `quota` column compares against a `QueryUsagePerDay` cap.
- **hot tables**: a job that touches three tables is counted against all three, so this is an upper
  bound on what each table costs you, useful for spotting the table everyone scans.
- **SCRIPT** parent jobs are excluded; their child jobs are counted individually.
- **bqtop used**: bytes bqtop billed for its own three queries per refresh. `INFORMATION_SCHEMA` queries
  have a 10 MiB minimum each, so a 60 s refresh costs well under a dollar a day.

## Not covered (yet)

BigQuery Storage Read API sessions and network egress do not appear in job metadata. If you stream
tables out with Spark, DuckDB, or Arrow clients, that spend is invisible here; Cloud Billing export is
the place to see it.

## License

MIT

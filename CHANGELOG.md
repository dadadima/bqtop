# Changelog

## 0.3.4 (2026-09-30)

- The wizard verifies a project before accepting it (looks like an id, BigQuery jobs can run there), so a
  folder name typed as project is caught with an explanation instead of failing later.
- Load errors in the TUI open a dialog with the full message and a hint (`e` reopens it) instead of a
  clipped one-line status; the last good screen stays.

## 0.3.3 (2026-09-30)

- `--init` for a folder now asks *which folder*, offering the folders above your default project by
  name, and picks a project inside it to run from automatically. `[source].folder` records the choice and
  `--check` warns when the billing project is not directly inside it.

## 0.3.2 (2026-09-30)

- `--init` redone after first-user feedback: what-to-watch comes first, numbered choices that also
  accept loose spellings (`on-demand`), one line per question, timezone and numbers validated, the
  watched folder is detected and shown, existing config asks before overwriting, check runs at the end.
- Ctrl-C aborts cleanly; config mistakes (e.g. an invalid timezone) and unexpected errors print one line
  instead of a traceback (`--debug` for the trace). `tzdata` bundled so the binary finds zones everywhere.

## 0.3.1 (2026-09-30)

- `[source].credentials_file`: run bqtop as a service account instead of Application Default
  Credentials, for setups where the user login expires.
- `--check` reports the identity actually used and the coverage: jobs and projects seen in the last 24h,
  with a hint when `JOBS_BY_FOLDER` only sees the billing project itself.

## 0.3.0 (2026-09-30)

- `bqtop --init` is an interactive setup: detects the gcloud project and timezone, asks source, scope,
  pricing and refresh, writes the config and runs `--check`. `--init -y` writes the example silently.
- dbt models panel: `d` swaps hot tables for cost per dbt model, parsed from dbt's query comment
  (`node_id`). Also in `--once` output and `--json` (`by_model`).
- Single-file binaries for macOS (arm64, x86_64) and Linux (arm64, x86_64) on every `v*` tag, via
  PyInstaller, attached to the GitHub release together with a Homebrew formula.
- Homebrew: `brew install dadadima/tap/bqtop`.
- Release workflow also publishes to PyPI once a trusted publisher is configured.

## 0.2.0 (2026-09-30)

- Local job store with incremental refresh; sort, filter and window changes never hit BigQuery.
- Pricing model: `auto` / `on_demand` / `slots`, per-project overrides, reservation-aware.
- `[budgets]` in USD/day for principals and projects, next to `[quotas]`.
- Cost sparkline, `/` filter, enter to drill down, job detail with full query, `?` help, `p` pause.
- `--demo`, `--check` with permission hints, `--watch`, `--json`, `-f`, multi-region.
- Tests, ruff, CI, screenshot from demo data.

## 0.1.0 (2026-09-30)

- First cut: INFORMATION_SCHEMA and audit-log sources, four panels, `--once`.

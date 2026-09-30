"""Turn Google API error text into one actionable sentence. Used by `--check` and the TUI error dialog."""

from __future__ import annotations

HINTS: tuple[tuple[str, str], ...] = (
    (
        "projectid must be non-empty",
        "BigQuery does not know that project: [source].billing_project must be a project *id* "
        "such as my-project-123, not a folder or display name",
    ),
    (
        "invalid project id",
        "billing_project must be a project *id* such as my-project-123, not a folder or display name",
    ),
    (
        "has not enabled bigquery",
        "that project has no BigQuery API enabled, or it is not a project id at all; check [source].billing_project",
    ),
    ("project not found", "check [source].billing_project: use the project id, not its name"),
    (
        "jobs.listall",
        "grant roles/bigquery.resourceViewer at that level (project, folder or org), "
        'or use scope = "user" / kind = "audit_log"',
    ),
    ("bigquery.jobs.create", "grant roles/bigquery.jobUser on [source].billing_project"),
    ("not found: table", "check [source].table (audit_log) or [source].projects / regions"),
    (
        "could not automatically determine credentials",
        "run `gcloud auth application-default login` or set GOOGLE_APPLICATION_CREDENTIALS",
    ),
    ("reauthentication", "run `gcloud auth application-default login` again"),
    ("invalid_grant", "your Google login expired: run `gcloud auth application-default login`"),
    ("has not been used in project", "enable the BigQuery API on [source].billing_project"),
    ("quota", "BigQuery quota hit in the billing project; wait or raise the quota"),
)


def hint_for(error_text: str) -> str:
    low = error_text.lower()
    for needle, hint in HINTS:
        if needle in low:
            return hint
    return "run `bqtop --check` for a diagnosis"

"""INFORMATION_SCHEMA.JOBS_BY_{PROJECT,FOLDER,ORGANIZATION,USER}. Real time, includes RUNNING jobs.
Permissions: bigquery.jobs.listAll at the matching level (roles/bigquery.resourceViewer); none for `user`."""

from __future__ import annotations

from bqtop.sources.base import QUERY_SNIPPET_CHARS, Source

_VIEW = {
    "project": "JOBS_BY_PROJECT",
    "folder": "JOBS_BY_FOLDER",
    "organization": "JOBS_BY_ORGANIZATION",
    "user": "JOBS_BY_USER",
}

_COLS = """
    creation_time,
    project_id,
    user_email as principal,
    job_id,
    job_type,
    statement_type,
    state,
    start_time,
    end_time,
    total_bytes_processed as bytes_processed,
    total_bytes_billed as bytes_billed,
    total_slot_ms as slot_ms,
    cache_hit,
    error_result.reason as error_code,
    substr(error_result.message, 1, 300) as error_message,
    {query_col} as query,
    array(
        select concat(t.project_id, '.', t.dataset_id, '.', t.table_id)
        from unnest(referenced_tables) as t
    ) as referenced_tables,
    if(destination_table.table_id is null, null,
       concat(destination_table.project_id, '.', destination_table.dataset_id, '.', destination_table.table_id)
    ) as destination_table,
    reservation_id
"""


class InformationSchemaSource(Source):
    def _views(self) -> list[str]:
        src = self.cfg.source
        name = _VIEW[src.scope]
        projects = src.projects if src.scope == "project" else [src.billing_project]
        return [f"`{p}`.`region-{r}`.INFORMATION_SCHEMA.{name}" for p in projects for r in src.regions]

    def _has_query_text(self) -> bool:
        return self.cfg.source.scope in ("project", "user")

    def base_sql(self) -> str:
        query_col = f"substr(query, 1, {QUERY_SNIPPET_CHARS})" if self._has_query_text() else "cast(null as string)"
        cols = _COLS.format(query_col=query_col)
        return "\nunion all\n".join(f"select {cols} from {v}\n{{where}}" for v in self._views())

    def time_where(self) -> str:
        # SCRIPT parents re-report their children's bytes; skip them to avoid double counting.
        return """where creation_time >= @floor
  and ((creation_time >= @since and creation_time < @until) or job_id in unnest(@pending))
  and (statement_type is null or statement_type != 'SCRIPT')"""

    def query_sql(self) -> str | None:
        if not self._has_query_text():
            return None
        return (
            "\nunion all\n".join(
                f"select query from {v} where project_id = @project_id and job_id = @job_id" for v in self._views()
            )
            + "\nlimit 1"
        )

    def describe(self) -> str:
        src = self.cfg.source
        regions = ",".join(src.regions)
        if src.scope == "project":
            return f"INFORMATION_SCHEMA.JOBS_BY_PROJECT × {len(src.projects)} ({regions})"
        return f"INFORMATION_SCHEMA.JOBS_BY_{src.scope.upper()} via {src.billing_project} ({regions})"

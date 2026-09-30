"""INFORMATION_SCHEMA.JOBS_BY_{PROJECT,FOLDER,ORGANIZATION,USER} source. Real time, includes RUNNING
jobs. Permissions: bigquery.jobs.listAll at the matching level (none for `user`)."""

from __future__ import annotations

from bqtop.sources.base import Source

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
    error_result.message as error_message,
    {query_col} as query,
    array(
        select concat(t.project_id, '.', t.dataset_id, '.', t.table_id)
        from unnest(referenced_tables) as t
    ) as referenced_tables,
    if(destination_table.table_id is null, null,
       concat(destination_table.project_id, '.', destination_table.dataset_id, '.', destination_table.table_id)
    ) as destination_table
"""

# SCRIPT parent jobs re-report their children's bytes; skip them to avoid double counting.
_WHERE = """
where creation_time >= @window_start
  and (statement_type is null or statement_type != 'SCRIPT')
"""


class InformationSchemaSource(Source):
    def _view(self, project: str) -> str:
        scope = self.cfg.source.scope
        name = {"project": "JOBS_BY_PROJECT", "folder": "JOBS_BY_FOLDER",
                "organization": "JOBS_BY_ORGANIZATION", "user": "JOBS_BY_USER"}[scope]
        return f"`{project}`.`region-{self.cfg.source.region}`.INFORMATION_SCHEMA.{name}"

    def base_sql(self) -> str:
        src = self.cfg.source
        # query text only exists in the project- and user-level views
        query_col = "query" if src.scope in ("project", "user") else "cast(null as string)"
        cols = _COLS.format(query_col=query_col)
        projects = src.projects if src.scope == "project" else [src.billing_project]
        selects = [f"select {cols} from {self._view(p)} {_WHERE}" for p in projects]
        return "\nunion all\n".join(selects)

    def describe(self) -> str:
        src = self.cfg.source
        if src.scope == "project":
            return f"INFORMATION_SCHEMA.JOBS_BY_PROJECT x{len(src.projects)} ({src.region})"
        return f"INFORMATION_SCHEMA.JOBS_BY_{src.scope.upper()} via {src.billing_project} ({src.region})"

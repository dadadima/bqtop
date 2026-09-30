"""BigQuery audit-log export: a `cloudaudit_googleapis_com_data_access` table filled by a Cloud Logging
sink (project, folder or org level). Sees completed jobs only (the DONE jobChange event), so there is
no RUNNING state. Permissions: dataViewer on the sink table, jobUser on the billing project.

Note: the sink table is day-partitioned, so an incremental fetch still scans the current day's
partition. Cost per refresh grows with the day's volume; 120 s refresh is a sensible default here."""

from __future__ import annotations

from bqtop.sources.base import QUERY_SNIPPET_CHARS, Source

_TABLE_RE = r"^projects/([^/]+)/datasets/([^/]+)/tables/([^/]+)$"

_SQL = """
select
    timestamp as creation_time,
    resource.labels.project_id as project_id,
    protopayload_auditlog.authenticationinfo.principalemail as principal,
    split(json_value(m, '$.jobChange.job.jobName'), '/')[safe_offset(3)] as job_id,
    json_value(m, '$.jobChange.job.jobConfig.type') as job_type,
    json_value(m, '$.jobChange.job.jobConfig.queryConfig.statementType') as statement_type,
    'DONE' as state,
    timestamp(json_value(m, '$.jobChange.job.jobStats.startTime')) as start_time,
    timestamp(json_value(m, '$.jobChange.job.jobStats.endTime')) as end_time,
    cast(json_value(m, '$.jobChange.job.jobStats.queryStats.totalProcessedBytes') as int64) as bytes_processed,
    cast(json_value(m, '$.jobChange.job.jobStats.queryStats.totalBilledBytes') as int64) as bytes_billed,
    cast(json_value(m, '$.jobChange.job.jobStats.totalSlotMs') as int64) as slot_ms,
    cast(json_value(m, '$.jobChange.job.jobStats.queryStats.cacheHit') as bool) as cache_hit,
    json_value(m, '$.jobChange.job.jobStatus.errorResult.code') as error_code,
    substr(json_value(m, '$.jobChange.job.jobStatus.errorResult.message'), 1, 300) as error_message,
    substr(json_value(m, '$.jobChange.job.jobConfig.queryConfig.query'), 1, {snippet}) as query,
    array(
        select regexp_replace(x, r'{table_re}', r'\\1.\\2.\\3')
        from unnest(json_value_array(m, '$.jobChange.job.jobStats.queryStats.referencedTables')) as x
    ) as referenced_tables,
    regexp_replace(json_value(m, '$.jobChange.job.jobConfig.queryConfig.destinationTable'),
                   r'{table_re}', r'\\1.\\2.\\3') as destination_table,
    json_value(m, '$.jobChange.job.jobStats.reservationUsage[0].name') as reservation_id
from (
    select timestamp, resource, protopayload_auditlog, protopayload_auditlog.metadatajson as m
    from `{table}`
    {{where}}
)
where json_value(m, '$.jobChange.job.jobConfig.queryConfig.statementType') is null
   or json_value(m, '$.jobChange.job.jobConfig.queryConfig.statementType') != 'SCRIPT'
"""

_QUERY_SQL = """
select json_value(protopayload_auditlog.metadatajson, '$.jobChange.job.jobConfig.queryConfig.query') as query
from `{table}`
where timestamp >= timestamp_sub(current_timestamp(), interval 8 day)
  and resource.labels.project_id = @project_id
  and json_value(protopayload_auditlog.metadatajson, '$.jobChange.job.jobName') like concat('%/jobs/', @job_id)
  and json_value(protopayload_auditlog.metadatajson, '$.jobChange.job.jobStatus.jobState') = 'DONE'
limit 1
"""


class AuditLogSource(Source):
    has_running_jobs = False

    def base_sql(self) -> str:
        return _SQL.format(table=self.cfg.source.table, snippet=QUERY_SNIPPET_CHARS, table_re=_TABLE_RE)

    def time_where(self) -> str:
        return """where timestamp >= @since and timestamp < @until
      and json_value(protopayload_auditlog.metadatajson, '$.jobChange.job.jobStatus.jobState') = 'DONE'"""

    def query_sql(self) -> str | None:
        return _QUERY_SQL.format(table=self.cfg.source.table)

    def describe(self) -> str:
        return f"audit log {self.cfg.source.table}"

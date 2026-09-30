"""BigQuery audit-log export source: a `cloudaudit_googleapis_com_data_access` table filled by a
Cloud Logging sink (project-, folder- or org-level). Completed jobs only (the DONE jobChange event).
Permissions: bigquery.dataViewer on the sink table plus jobUser on the billing project."""

from __future__ import annotations

from bqtop.sources.base import Source

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
    json_value(m, '$.jobChange.job.jobStatus.errorResult.message') as error_message,
    json_value(m, '$.jobChange.job.jobConfig.queryConfig.query') as query,
    array(
        select regexp_replace(x, r'^projects/([^/]+)/datasets/([^/]+)/tables/([^/]+)$', r'\\1.\\2.\\3')
        from unnest(json_value_array(m, '$.jobChange.job.jobStats.queryStats.referencedTables')) as x
    ) as referenced_tables,
    regexp_replace(
        json_value(m, '$.jobChange.job.jobConfig.queryConfig.destinationTable'),
        r'^projects/([^/]+)/datasets/([^/]+)/tables/([^/]+)$', r'\\1.\\2.\\3'
    ) as destination_table
from (
    select timestamp, resource, protopayload_auditlog, protopayload_auditlog.metadatajson as m
    from `{table}`
    where timestamp >= @window_start
      and json_value(protopayload_auditlog.metadatajson, '$.jobChange.job.jobStatus.jobState') = 'DONE'
)
where json_value(m, '$.jobChange.job.jobConfig.queryConfig.statementType') is null
   or json_value(m, '$.jobChange.job.jobConfig.queryConfig.statementType') != 'SCRIPT'
"""


class AuditLogSource(Source):
    def base_sql(self) -> str:
        return _SQL.format(table=self.cfg.source.table)

    def describe(self) -> str:
        return f"audit log {self.cfg.source.table}"

    @property
    def has_running_jobs(self) -> bool:
        return False

from __future__ import annotations

from bqtop.config import Config
from bqtop.sources.audit_log import AuditLogSource
from bqtop.sources.base import Source
from bqtop.sources.information_schema import InformationSchemaSource


def make_source(cfg: Config) -> Source:
    if cfg.source.kind == "audit_log":
        return AuditLogSource(cfg)
    return InformationSchemaSource(cfg)


__all__ = ["Source", "make_source"]

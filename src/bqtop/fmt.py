from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo


def bytes_(n: int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(n) < 1024 or unit == "PiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def money(x: float | None) -> str:
    if x is None:
        return "-"
    if abs(x) < 0.01 and x != 0:
        return "<$0.01"
    return f"${x:,.2f}"


def duration(s: float | None) -> str:
    if s is None:
        return "-"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.1f}m"
    return f"{s / 3600:.1f}h"


def slot_hours(ms: int | None) -> str:
    if not ms:
        return "-"
    return f"{ms / 3_600_000:.2f}"


def pct(num: float, den: float) -> str:
    if not den:
        return "-"
    return f"{num / den * 100:.0f}%"


def clock(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(tz).strftime("%H:%M:%S")


_LEADING_COMMENT = re.compile(r"^\s*(?:/\*.*?\*/\s*|--[^\n]*\n\s*)+", re.S)


def one_line(text: str | None, width: int) -> str:
    """Collapse whitespace, drop leading comment blocks (dbt's /* {...} */ header), truncate."""
    if not text:
        return ""
    t = " ".join(_LEADING_COMMENT.sub("", text, count=1).split()) or " ".join(text.split())
    return t if len(t) <= width else t[: width - 1] + "…"


def short_principal(p: str) -> str:
    """svc-airflow-prod@cdo-de-ingest-prod.iam.gserviceaccount.com -> svc-airflow-prod@cdo-de-ingest-prod"""
    return p.replace(".iam.gserviceaccount.com", "") if p else "-"


def stmt(job) -> str:
    """Short job kind: select, ctas, merge, load, copy, extract..."""
    st = (job.statement_type or job.job_type or "-").lower()
    return {
        "create_table_as_select": "ctas",
        "insert": "insert",
        "select": "select",
        "merge": "merge",
        "delete": "delete",
        "update": "update",
        "truncate_table": "truncate",
        "drop_table": "drop",
        "create_view": "view",
        "create_table": "create",
        "alter_table": "alter",
        "script": "script",
        "query": "query",
        "load": "load",
        "copy": "copy",
        "extract": "extract",
    }.get(st, st)


def when(dt: datetime | None, tz: ZoneInfo) -> str:
    """HH:MM:SS if today, else MM-DD HH:MM."""
    if dt is None:
        return "-"
    local = dt.astimezone(tz)
    return local.strftime("%H:%M:%S") if local.date() == datetime.now(tz).date() else local.strftime("%m-%d %H:%M")

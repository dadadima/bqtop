from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_UNITS = {
    "b": 1,
    "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12, "pb": 10**15,
    "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40, "pib": 2**50,
}


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "bqtop" / "config.toml"


def parse_size(value: int | float | str) -> int:
    """'5 TiB' / '500GB' / 1024 -> bytes."""
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]*)\s*", str(value))
    if not m:
        raise ValueError(f"bad size: {value!r}")
    unit = (m.group(2) or "b").lower()
    if unit not in _UNITS:
        raise ValueError(f"unknown unit in size: {value!r}")
    return int(float(m.group(1)) * _UNITS[unit])


@dataclass
class SourceConfig:
    kind: str = "information_schema"
    billing_project: str = ""
    region: str = "us"
    scope: str = "project"
    projects: list[str] = field(default_factory=list)
    table: str = ""


@dataclass
class UIConfig:
    refresh_seconds: int = 60
    window_hours: float = 24
    timezone: str = "UTC"
    top_n: int = 15
    stream_rows: int = 40


@dataclass
class Config:
    source: SourceConfig
    ui: UIConfig
    price_per_tib: float = 6.25
    quotas: dict[str, int] = field(default_factory=dict)
    path: Path | None = None


class ConfigError(Exception):
    pass


def load(path: str | os.PathLike | None = None) -> Config:
    candidates = [Path(path)] if path else [Path("bqtop.toml"), default_config_path()]
    for p in candidates:
        if p.is_file():
            return _parse(p)
    raise ConfigError(
        "no config found (looked at: " + ", ".join(str(c) for c in candidates) + "). "
        "Run `bqtop --init` to write a starter config."
    )


def _parse(p: Path) -> Config:
    raw = tomllib.loads(p.read_text())
    src = SourceConfig(**raw.get("source", {}))
    ui = UIConfig(**raw.get("ui", {}))
    price = float(raw.get("pricing", {}).get("on_demand_usd_per_tib", 6.25))
    quotas = {k: parse_size(v) for k, v in raw.get("quotas", {}).items()}

    if not src.billing_project:
        raise ConfigError("[source].billing_project is required")
    if src.kind not in ("information_schema", "audit_log"):
        raise ConfigError(f"[source].kind must be information_schema or audit_log, got {src.kind!r}")
    if src.kind == "audit_log" and not src.table:
        raise ConfigError("[source].table is required for kind = audit_log")
    if src.kind == "information_schema":
        if src.scope not in ("project", "folder", "organization", "user"):
            raise ConfigError(f"[source].scope must be project|folder|organization|user, got {src.scope!r}")
        if src.scope == "project" and not src.projects:
            src.projects = [src.billing_project]
    return Config(source=src, ui=ui, price_per_tib=price, quotas=quotas, path=p)

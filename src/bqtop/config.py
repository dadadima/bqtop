from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from bqtop.pricing import Pricing

_UNITS = {
    "b": 1,
    "kb": 10**3,
    "mb": 10**6,
    "gb": 10**9,
    "tb": 10**12,
    "pb": 10**15,
    "kib": 2**10,
    "mib": 2**20,
    "gib": 2**30,
    "tib": 2**40,
    "pib": 2**50,
}
KINDS = ("information_schema", "audit_log", "demo")
SCOPES = ("project", "folder", "organization", "user")


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


def parse_money(value: int | float | str) -> float:
    """'$50' / '50 usd' / 50 -> 50.0 (per day)."""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.fullmatch(r"\s*\$?\s*([\d.]+)\s*(usd|/day|per day)?\s*", str(value), re.I)
    if not m:
        raise ValueError(f"bad amount: {value!r}")
    return float(m.group(1))


@dataclass
class SourceConfig:
    kind: str = "information_schema"
    billing_project: str = ""
    region: str = "us"
    regions: list[str] = field(default_factory=list)
    scope: str = "project"
    projects: list[str] = field(default_factory=list)
    table: str = ""
    credentials_file: str = ""  # service-account key; default is Application Default Credentials

    def __post_init__(self) -> None:
        if not self.regions:
            self.regions = [self.region]


@dataclass
class UIConfig:
    refresh_seconds: int = 60
    window_hours: float = 24
    timezone: str = "UTC"
    top_n: int = 15
    stream_rows: int = 40
    max_window_hours: float = 168
    timeline_buckets: int = 48


@dataclass
class Config:
    source: SourceConfig
    ui: UIConfig
    pricing: Pricing = field(default_factory=Pricing)
    quotas: dict[str, int] = field(default_factory=dict)  # project -> bytes per day
    budgets: dict[str, float] = field(default_factory=dict)  # principal or project -> USD per day
    path: Path | None = None

    @classmethod
    def demo(cls) -> Config:
        cfg = cls(source=SourceConfig(kind="demo", billing_project="demo"), ui=UIConfig(refresh_seconds=5))
        cfg.quotas = {"acme-agents-prod": parse_size("2 TiB"), "acme-anl-dev": parse_size("1 TiB")}
        cfg.budgets = {"svc-agent-reader@acme-agents-prod.iam.gserviceaccount.com": 5.0, "acme-std-prod": 40.0}
        cfg.pricing.projects["acme-std-prod"] = type(cfg.pricing.default)(mode="auto", slot_usd_per_hour=0.04)
        return cfg


class ConfigError(Exception):
    pass


def load(path: str | os.PathLike | None = None) -> Config:
    candidates = [Path(path)] if path else [Path("bqtop.toml"), default_config_path()]
    for p in candidates:
        if p.is_file():
            return _parse(p)
    raise ConfigError(
        "no config found (looked at: " + ", ".join(str(c) for c in candidates) + "). "
        "Run `bqtop --init` to write a starter config, or `bqtop --demo` to look around first."
    )


def _parse(p: Path) -> Config:
    try:
        raw = tomllib.loads(p.read_text())
        src = SourceConfig(**raw.get("source", {}))
        ui = UIConfig(**raw.get("ui", {}))
        pricing = Pricing.from_toml(raw.get("pricing", {}))
        quotas = {k: parse_size(v) for k, v in raw.get("quotas", {}).items()}
        budgets = {k: parse_money(v) for k, v in raw.get("budgets", {}).items()}
    except (TypeError, ValueError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{p}: {e}") from e

    if src.kind not in KINDS:
        raise ConfigError(f"[source].kind must be one of {KINDS}, got {src.kind!r}")
    if src.kind != "demo" and not src.billing_project:
        raise ConfigError("[source].billing_project is required")
    if src.credentials_file:
        src.credentials_file = os.path.expanduser(src.credentials_file)
        if not os.path.isfile(src.credentials_file):
            raise ConfigError(f"[source].credentials_file not found: {src.credentials_file}")
    if src.kind == "audit_log" and not src.table:
        raise ConfigError("[source].table is required for kind = audit_log")
    if src.kind == "information_schema":
        if src.scope not in SCOPES:
            raise ConfigError(f"[source].scope must be one of {SCOPES}, got {src.scope!r}")
        if src.scope == "project" and not src.projects:
            src.projects = [src.billing_project]
    if ui.max_window_hours < ui.window_hours:
        ui.max_window_hours = ui.window_hours
    return Config(source=src, ui=ui, pricing=pricing, quotas=quotas, budgets=budgets, path=p)

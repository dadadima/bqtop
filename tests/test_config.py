from pathlib import Path

import pytest

from bqtop.config import Config, ConfigError, load, parse_money, parse_size


def test_parse_size():
    assert parse_size("5 TiB") == 5 * 2**40
    assert parse_size("500GB") == 500 * 10**9
    assert parse_size(1024) == 1024
    with pytest.raises(ValueError):
        parse_size("ten bananas")


def test_parse_money():
    assert parse_money("$50") == 50.0
    assert parse_money("12.5 usd") == 12.5
    assert parse_money(7) == 7.0


def test_load_information_schema_defaults(tmp_path: Path):
    p = tmp_path / "bqtop.toml"
    p.write_text('[source]\nkind = "information_schema"\nbilling_project = "p1"\n')
    cfg = load(p)
    assert cfg.source.projects == ["p1"]
    assert cfg.source.regions == ["us"]
    assert cfg.pricing.default.mode == "auto"
    assert cfg.ui.max_window_hours >= cfg.ui.window_hours


def test_load_full(tmp_path: Path):
    p = tmp_path / "bqtop.toml"
    p.write_text(
        '[source]\nkind = "audit_log"\nbilling_project = "adm"\ntable = "adm.logs.t"\nregions = ["us", "eu"]\n'
        '[pricing]\nmode = "on_demand"\non_demand_usd_per_tib = 5\n'
        '[pricing.projects]\n"dbt" = { mode = "slots", slot_usd_per_hour = 0.04 }\n'
        '[quotas]\n"agents" = "5 TiB"\n[budgets]\n"svc@x" = "$10"\n'
        '[ui]\ntimezone = "Europe/Brussels"\nwindow_hours = 6\n'
    )
    cfg = load(p)
    assert cfg.source.regions == ["us", "eu"]
    assert cfg.pricing.rate("dbt").mode == "slots"
    assert cfg.pricing.rate("other").on_demand_usd_per_tib == 5
    assert cfg.quotas["agents"] == 5 * 2**40
    assert cfg.budgets["svc@x"] == 10.0
    assert cfg.ui.timezone == "Europe/Brussels"


@pytest.mark.parametrize(
    "body",
    [
        '[source]\nkind = "nope"\nbilling_project = "p"\n',
        '[source]\nkind = "audit_log"\nbilling_project = "p"\n',
        '[source]\nkind = "information_schema"\n',
        '[source]\nkind = "information_schema"\nbilling_project = "p"\nscope = "galaxy"\n',
        '[source]\nkind = "information_schema"\nbilling_project = "p"\n[pricing]\nmode = "free"\n',
    ],
)
def test_load_errors(tmp_path: Path, body: str):
    p = tmp_path / "bqtop.toml"
    p.write_text(body)
    with pytest.raises(ConfigError):
        load(p)


def test_demo_config():
    cfg = Config.demo()
    assert cfg.source.kind == "demo"
    assert cfg.quotas and cfg.budgets


def test_credentials_file(tmp_path: Path):
    key = tmp_path / "key.json"
    key.write_text("{}")
    p = tmp_path / "bqtop.toml"
    p.write_text(f'[source]\nbilling_project = "p"\ncredentials_file = "{key}"\n')
    assert load(p).source.credentials_file == str(key)
    p.write_text('[source]\nbilling_project = "p"\ncredentials_file = "/nope/key.json"\n')
    with pytest.raises(ConfigError):
        load(p)

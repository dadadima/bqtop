"""Cost model. BigQuery bills analysis either on demand (bytes) or via slots (Editions). `auto` picks
per job: a job that ran in a reservation is priced in slot-hours, anything else on demand."""

from __future__ import annotations

from dataclasses import dataclass, field

from bqtop.model import Job

MODES = ("auto", "on_demand", "slots")
# published pay-as-you-go slot prices, USD per slot-hour, for reference in configs
EDITION_SLOT_PRICES = {"standard": 0.04, "enterprise": 0.06, "enterprise_plus": 0.10}


@dataclass
class Rate:
    mode: str = "auto"
    on_demand_usd_per_tib: float = 6.25
    slot_usd_per_hour: float = 0.06


@dataclass
class Pricing:
    default: Rate = field(default_factory=Rate)
    projects: dict[str, Rate] = field(default_factory=dict)

    def rate(self, project_id: str) -> Rate:
        return self.projects.get(project_id, self.default)

    def cost(self, job: Job) -> float:
        r = self.rate(job.project_id)
        mode = r.mode
        if mode == "auto":
            mode = "slots" if job.reservation_id else "on_demand"
        if mode == "slots":
            return job.slot_ms / 3_600_000 * r.slot_usd_per_hour
        return job.bytes_billed / 2**40 * r.on_demand_usd_per_tib

    def describe(self) -> str:
        r = self.default
        if r.mode == "on_demand":
            return f"on-demand ${r.on_demand_usd_per_tib}/TiB"
        if r.mode == "slots":
            return f"slots ${r.slot_usd_per_hour}/slot-h"
        return f"auto: ${r.on_demand_usd_per_tib}/TiB, reservations ${r.slot_usd_per_hour}/slot-h"

    @classmethod
    def from_toml(cls, raw: dict) -> Pricing:
        def rate(d: dict, base: Rate) -> Rate:
            mode = d.get("mode", base.mode)
            if mode not in MODES:
                raise ValueError(f"pricing mode must be one of {MODES}, got {mode!r}")
            return Rate(
                mode=mode,
                on_demand_usd_per_tib=float(d.get("on_demand_usd_per_tib", base.on_demand_usd_per_tib)),
                slot_usd_per_hour=float(d.get("slot_usd_per_hour", base.slot_usd_per_hour)),
            )

        default = rate(raw, Rate())
        projects = {p: rate(d, default) for p, d in raw.get("projects", {}).items()}
        return cls(default=default, projects=projects)

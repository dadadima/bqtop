"""Render docs/screenshot.svg from the demo source (no GCP needed): `uv run python scripts/screenshot.py`."""

import asyncio
from pathlib import Path

from bqtop.app import BqTop
from bqtop.config import Config
from bqtop.sources import make_source


async def main() -> None:
    cfg = Config.demo()
    cfg.ui.window_hours = 6
    app = BqTop(cfg, make_source(cfg))
    async with app.run_test(size=(190, 52)) as pilot:
        for _ in range(40):
            await pilot.pause(0.25)
            if app.snap:
                break
        await pilot.pause(0.5)
        out = Path(__file__).resolve().parents[1] / "docs" / "screenshot.svg"
        out.parent.mkdir(exist_ok=True)
        app.save_screenshot(str(out))
        print(f"wrote {out}")


asyncio.run(main())

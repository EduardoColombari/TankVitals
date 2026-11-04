"""`python -m app.rules`"""

from __future__ import annotations

import asyncio

from app.core.runtime import run_service

from .runner import run
from .settings import RulesSettings


async def main(stop: asyncio.Event) -> None:
    await run(RulesSettings(), stop)


if __name__ == "__main__":
    run_service(main)

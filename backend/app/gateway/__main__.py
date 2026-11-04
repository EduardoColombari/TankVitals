"""`python -m app.gateway`"""

from __future__ import annotations

import asyncio

from app.core.runtime import run_service

from .runner import run_forever
from .settings import GatewaySettings


async def main(stop: asyncio.Event) -> None:
    await run_forever(GatewaySettings(), stop)


if __name__ == "__main__":
    run_service(main)

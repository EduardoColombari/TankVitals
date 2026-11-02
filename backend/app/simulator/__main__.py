"""`python -m app.simulator`"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.runtime import run_service

from .mqtt import run_forever
from .service import SimulatorService
from .settings import SimulatorSettings
from .world import SimWorld

logger = logging.getLogger("app.simulator")


async def main(stop: asyncio.Event) -> None:
    settings = SimulatorSettings()
    # O relógio simulado parte do relógio real, escalado pela velocidade. Assim o
    # ciclo de dia e noite continua de onde estava se o simulador reiniciar.
    start_minute = time.time() * settings.sim_speed / 60.0
    world = SimWorld(seed=settings.sim_seed, start_minute=start_minute)

    async def not_connected(*_: object) -> None:
        raise ConnectionError("sem conexão com o Mosquitto")

    service = SimulatorService(world, not_connected, speed=settings.sim_speed)
    logger.info(
        "Simulador no ar: SIM_SPEED=%s (1 s real = %.2f min simulados; um dia dura %.1f min reais)",
        settings.sim_speed,
        settings.sim_speed / 60.0,
        24 * 60 / settings.sim_speed,
    )
    await run_forever(settings, service, stop)


if __name__ == "__main__":
    run_service(main)

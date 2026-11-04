"""Partida comum dos serviços: `python -m app.<serviço>` (BACK-01).

* no Windows, o `aiomqtt` não funciona com o laço de eventos padrão
  (Proactor); troca pelo Selector, para o serviço rodar igual no Windows e no
  Linux;
* SIGINT e SIGTERM (o `docker stop`) viram um evento de parada, para o
  serviço se despedir direito em vez de morrer no meio.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_service(main: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    """Roda `main(stop)` até acabar ou até chegar SIGINT/SIGTERM."""
    configure_logging()
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows não tem add_signal_handler
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        await main(stop)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


async def first_exit(*coros: Awaitable[Any]) -> None:
    """Roda tudo junto e, quando o primeiro termina (ou falha), cancela o resto.

    Se quem terminou falhou, a exceção sobe: é assim que um erro de conexão
    numa tarefa derruba a outra e deixa o laço de reconexão agir.
    """
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()  # type: ignore[misc]


def env(name: str, default: str | None = None) -> str:
    """Variável de ambiente obrigatória, com mensagem que diz o que fazer."""
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"Falta a variável {name}. Rode scripts/gerar_segredos.py e suba pelo compose.")
    return value

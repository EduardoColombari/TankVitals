"""O laço do simulador: um passo por vez, uma mensagem por dispositivo (IOT-01).

Aqui não tem MQTT: a publicação entra por uma função (`PublishFn`). O adaptador
de verdade, com o `aiomqtt`, está em `mqtt.py`. Assim o laço roda nos testes
com uma função falsa.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.core.envelope import dumps, make_envelope, status_envelope
from app.core.topics import SIM_STATUS_TOPIC, telemetry_topic

from .commands import InvalidCommand, parse_command
from .devices import DEVICES_BY_ID
from .world import CommandRejected, Reading, SimWorld

logger = logging.getLogger(__name__)

# (tópico, corpo, qos, retained)
PublishFn = Callable[[str, str, int, bool], Awaitable[None]]

TELEMETRY_QOS = 0  # chega uma por segundo; a próxima cobre a perdida (§13)
STATUS_QOS = 1


class SimulatorService:
    def __init__(
        self,
        world: SimWorld,
        publish: PublishFn,
        *,
        speed: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if speed <= 0:
            raise ValueError("SIM_SPEED precisa ser maior que zero")
        self.world = world
        self._publish = publish
        # 1 leitura = 1 minuto simulado, então com SIM_SPEED=60 sai uma leitura por segundo real.
        self.interval_s = 60.0 / speed
        self._monotonic = monotonic
        self._sleep = sleep

    def bind(self, publish: PublishFn) -> None:
        """Troca a função de publicar. O adaptador chama a cada (re)conexão com o Mosquitto."""
        self._publish = publish

    # --- publicação ------------------------------------------------------------

    async def _publish_reading(self, reading: Reading) -> None:
        device = reading.device
        envelope = make_envelope("telemetry", device.id, dict(reading.payload))
        await self._publish(telemetry_topic(device.zone, device.kind, device.id), dumps(envelope), TELEMETRY_QOS, False)

    async def publish_step(self) -> None:
        """Avança o mundo 1 minuto simulado e publica os 9 dispositivos."""
        for reading in self.world.step():
            await self._publish_reading(reading)

    async def announce(self, online: bool) -> None:
        """Status do simulador, retido: quem se conecta depois já sabe se ele está no ar."""
        await self._publish(SIM_STATUS_TOPIC, dumps(status_envelope(online)), STATUS_QOS, True)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Publica um passo a cada `interval_s`, sem acumular atraso, até `stop`.

        Se o laço atrasar (a máquina travou), não dispara uma rajada para
        recuperar: o tempo simulado é por passo, não por relógio.
        """
        next_at = self._monotonic()
        while stop is None or not stop.is_set():
            await self.publish_step()
            next_at += self.interval_s
            delay = next_at - self._monotonic()
            if delay < -5 * self.interval_s:
                next_at = self._monotonic()
                delay = 0.0
            if delay > 0:
                await self._sleep(delay)
            else:
                await self._sleep(0)  # cede o laço de eventos para ouvir os comandos

    # --- comandos (IOT-03) ------------------------------------------------------

    async def handle_cmd(self, topic: str, raw: bytes | bytearray | str) -> bool:
        """Aplica um comando do `/cmd`. Devolve `True` se foi aplicado.

        Comando malformado ou recusado vira log e pronto: nunca levanta.
        """
        try:
            command = parse_command(topic, raw)
            self.world.apply_command(command.device, command.command, command.value)
        except (InvalidCommand, CommandRejected) as err:
            logger.warning("Comando ignorado em %s: %s", topic, err)
            return False
        except Exception:  # noqa: BLE001 - um comando não pode derrubar o simulador
            logger.exception("Erro inesperado ao aplicar o comando de %s", topic)
            return False

        logger.info(
            "Comando aplicado: %s %s=%r (origem %s, usuário %s)",
            command.device,
            command.command,
            command.value,
            command.origin,
            command.user,
        )
        # Atuador confirma já, sem esperar o próximo passo: é a prova de que o
        # comando chegou. A bomba confirma no passo seguinte, com as leituras novas.
        if DEVICES_BY_ID[command.device].kind == "actuator":
            try:
                await self._publish_reading(self.world.actuator_reading(command.device))
            except Exception:  # noqa: BLE001 - o próximo passo confirma de qualquer jeito
                logger.warning("Não consegui publicar a confirmação imediata de %s", command.device)
        return True

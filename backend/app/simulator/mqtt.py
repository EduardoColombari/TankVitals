"""Adaptador do simulador com o Mosquitto (aiomqtt).

O estado do mundo (`SimWorld`) é criado uma vez e sobrevive às reconexões: se o
Mosquitto reiniciar, o simulador volta de onde parou, com os atuadores no mesmo
estado.
"""

from __future__ import annotations

import asyncio
import logging

import aiomqtt

from app.core.envelope import dumps, status_envelope
from app.core.runtime import first_exit
from app.core.topics import COMMAND_SUBSCRIPTION, SIM_STATUS_TOPIC

from .service import STATUS_QOS, SimulatorService
from .settings import SimulatorSettings

logger = logging.getLogger(__name__)


async def run_forever(settings: SimulatorSettings, service: SimulatorService, stop: asyncio.Event) -> None:
    """Conecta, publica e escuta os comandos. Reconecta sozinho se o Mosquitto cair."""
    backoff = 1.0
    while not stop.is_set():
        # Last Will (§13): se o simulador cair sem se despedir, o Mosquitto publica
        # `{"online": false}` por ele. O `ts` é o da conexão.
        will = aiomqtt.Will(
            topic=SIM_STATUS_TOPIC,
            payload=dumps(status_envelope(False)),
            qos=STATUS_QOS,
            retain=True,
        )
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_username or None,
                password=settings.mqtt_password or None,
                identifier=settings.sim_client_id,
                will=will,
                keepalive=30,
            ) as client:
                logger.info("Conectado ao Mosquitto em %s:%s", settings.mqtt_host, settings.mqtt_port)
                backoff = 1.0
                await client.subscribe(COMMAND_SUBSCRIPTION, qos=1)

                async def publish(topic: str, body: str, qos: int, retain: bool) -> None:
                    await client.publish(topic, body, qos=qos, retain=retain)

                # A função de publicar é a deste cliente, e muda a cada conexão.
                service.bind(publish)
                await service.announce(True)

                async def listen() -> None:
                    async for message in client.messages:
                        payload = message.payload
                        raw = bytes(payload) if isinstance(payload, (bytes, bytearray)) else str(payload or "").encode()
                        await service.handle_cmd(message.topic.value, raw)

                async def publish_loop() -> None:
                    await service.run(stop)

                async def wait_stop() -> None:
                    await stop.wait()

                try:
                    await first_exit(listen(), publish_loop(), wait_stop())
                finally:
                    if stop.is_set():
                        # Saída combinada: avisa que caiu, em vez de deixar para o Last Will.
                        try:
                            await service.announce(False)
                        except aiomqtt.MqttError:
                            pass
        except aiomqtt.MqttError as err:
            if stop.is_set():
                break
            logger.warning("Mosquitto indisponível (%s); tentando de novo em %.0f s", err, backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)

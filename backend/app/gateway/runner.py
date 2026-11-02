"""Adaptador do gateway com o Mosquitto (aiomqtt) e o RabbitMQ (aio-pika).

Reconexão:

* **RabbitMQ reiniciou:** a conexão robusta do aio-pika reconecta e retoma o
  consumidor sozinha, e o `publish` tenta de novo enquanto isso. O gateway não
  cai.
* **Mosquitto reiniciou:** o laço daqui reconecta com espera crescente. Enquanto
  o MQTT está fora, o consumo de `q.commands` fica parado: os comandos esperam
  na fila (persistente) em vez de se perderem.
"""

from __future__ import annotations

import asyncio
import logging

import aiomqtt
from aio_pika.abc import AbstractIncomingMessage

from app.core.rabbit import RabbitBus
from app.core.topics import Q_COMMANDS, SIM_STATUS_TOPIC, TELEMETRY_SUBSCRIPTION

from .service import CommandOutcome, GatewayService
from .settings import GatewaySettings

logger = logging.getLogger(__name__)


async def run_forever(settings: GatewaySettings, stop: asyncio.Event) -> None:
    bus = await RabbitBus.connect(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        user=settings.rabbitmq_user,
        password=settings.rabbitmq_password,
        vhost=settings.rabbitmq_vhost,
        prefetch=20,
        name="gateway",
    )

    async def rabbit_publish(exchange, routing_key, body, *, headers=None, message_id=None, persistent=False):
        await bus.publish(exchange, routing_key, body, headers=headers, message_id=message_id, persistent=persistent)

    service = GatewayService(rabbit_publish)
    backoff = 1.0
    try:
        while not stop.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                    username=settings.mqtt_username or None,
                    password=settings.mqtt_password or None,
                    identifier=settings.gateway_client_id,
                    keepalive=30,
                ) as client:
                    logger.info("Conectado ao Mosquitto em %s:%s", settings.mqtt_host, settings.mqtt_port)
                    backoff = 1.0
                    await client.subscribe(TELEMETRY_SUBSCRIPTION, qos=0)
                    await client.subscribe(SIM_STATUS_TOPIC, qos=1)

                    async def mqtt_publish(topic: str, body: bytes, qos: int) -> None:
                        await client.publish(topic, body, qos=qos)

                    service.bind_mqtt(mqtt_publish)

                    async def on_command(message: AbstractIncomingMessage) -> None:
                        outcome = await service.on_command(message.routing_key or "", message.body)
                        if outcome is CommandOutcome.ACK:
                            await message.ack()
                        elif outcome is CommandOutcome.REJECT:
                            await message.nack(requeue=False)  # cai na DLQ
                        else:
                            await message.nack(requeue=True)

                    # Só consome comandos enquanto o MQTT está de pé.
                    queue, tag = await bus.consume(Q_COMMANDS, on_command)
                    try:
                        await _pump_messages(client, service, stop)
                    finally:
                        service.bind_mqtt(None)
                        try:
                            await queue.cancel(tag)
                        except Exception:  # noqa: BLE001 - a conexão já pode ter caído
                            logger.debug("não deu para cancelar o consumidor", exc_info=True)
            except aiomqtt.MqttError as err:
                if stop.is_set():
                    break
                logger.warning("Mosquitto indisponível (%s); tentando de novo em %.0f s", err, backoff)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
    finally:
        await bus.close()


async def _pump_messages(client: aiomqtt.Client, service: GatewayService, stop: asyncio.Event) -> None:
    """Repassa as mensagens do MQTT até parar ou a conexão cair (aí sobe `MqttError`)."""

    async def forward() -> None:
        async for message in client.messages:
            payload = message.payload
            raw = bytes(payload) if isinstance(payload, (bytes, bytearray)) else str(payload or "").encode()
            try:
                await service.on_mqtt_message(message.topic.value, raw)
            except ConnectionError as err:
                # RabbitMQ fora do ar por tempo demais: a telemetria é QoS 0, a próxima cobre a perdida.
                logger.error("Telemetria descartada, RabbitMQ indisponível: %s", err)

    forwarder = asyncio.ensure_future(forward())
    waiter = asyncio.ensure_future(stop.wait())
    try:
        done, _ = await asyncio.wait({forwarder, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if forwarder in done:
            forwarder.result()  # relança o MqttError, se foi ele
    finally:
        for task in (forwarder, waiter):
            task.cancel()
        await asyncio.gather(forwarder, waiter, return_exceptions=True)

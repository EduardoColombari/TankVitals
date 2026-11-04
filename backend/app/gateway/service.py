"""O trabalho do gateway, sem saber de aiomqtt nem de aio-pika (IOT-02).

As duas pontas entram por portas (funções), e o adaptador de verdade está em
`runner.py`. Assim a lógica, inclusive "confirmar só depois de publicar", roda
nos testes com portas falsas.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum

from app.core.topics import EX_DLX

from .bridge import BadCommand, DeadLetter, Forward, route_command, route_mqtt_message

logger = logging.getLogger(__name__)

# exchange, routing key, corpo, headers, message_id, persistente
RabbitPublish = Callable[..., Awaitable[None]]
# tópico, corpo, qos
MqttPublish = Callable[[str, bytes, int], Awaitable[None]]


class CommandOutcome(Enum):
    ACK = "ack"  # entregue ao MQTT: confirma
    REJECT = "reject"  # inválido: nack sem requeue, cai na DLQ
    REQUEUE = "requeue"  # o MQTT falhou: devolve à fila e tenta de novo depois


class GatewayService:
    def __init__(self, rabbit_publish: RabbitPublish, mqtt_publish: MqttPublish | None = None):
        self._rabbit_publish = rabbit_publish
        self._mqtt_publish = mqtt_publish

    def bind_mqtt(self, mqtt_publish: MqttPublish | None) -> None:
        """Troca a porta do MQTT a cada (re)conexão; `None` enquanto o Mosquitto está fora."""
        self._mqtt_publish = mqtt_publish

    # --- MQTT → RabbitMQ ---------------------------------------------------------

    async def on_mqtt_message(self, topic: str, payload: bytes | bytearray) -> None:
        """Valida e republica. Mensagem inválida vai para a DLQ com o corpo original e o motivo."""
        decision: Forward | DeadLetter = route_mqtt_message(topic, payload)
        if isinstance(decision, DeadLetter):
            logger.warning("Mensagem inválida em %s: %s", topic, decision.reason)
            await self._rabbit_publish(
                EX_DLX,
                "invalid",
                decision.body,
                headers=decision.headers,
                persistent=True,
            )
            return
        await self._rabbit_publish(
            decision.exchange,
            decision.routing_key,
            decision.body,
            message_id=decision.message_id,
            headers=decision.headers or None,
        )

    # --- RabbitMQ → MQTT ---------------------------------------------------------

    async def on_command(self, routing_key: str, body: bytes | bytearray) -> CommandOutcome:
        """Entrega um comando ao `/cmd`. O ack só pode acontecer depois de o MQTT aceitar."""
        try:
            command = route_command(routing_key, body)
        except BadCommand as err:
            logger.warning("Comando inválido em %s: %s", routing_key, err)
            return CommandOutcome.REJECT
        if self._mqtt_publish is None:
            return CommandOutcome.REQUEUE
        try:
            await self._mqtt_publish(command.topic, command.body, 1)
        except Exception as err:  # noqa: BLE001 - qualquer falha do MQTT devolve o comando à fila
            logger.warning("Não consegui publicar o comando em %s (%s); devolvendo à fila", command.topic, err)
            return CommandOutcome.REQUEUE
        logger.info("Comando entregue em %s", command.topic)
        return CommandOutcome.ACK

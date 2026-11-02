"""Conexão com o RabbitMQ (aio-pika), igual para todos os serviços (BACK-01).

Duas decisões que valem para o projeto inteiro:

* **Ninguém declara exchange nem fila.** A estrutura vem do `definitions.json`
  (PLAT-05) e cada usuário só tem permissão para ler a sua fila e escrever na
  sua exchange. Por isso aqui só se *pega* o objeto (`ensure=False`), sem
  mandar declaração nenhuma ao broker.
* **Conexão robusta.** `connect_robust` reconecta sozinho quando o RabbitMQ
  reinicia, refaz o canal e retoma os consumidores.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

logger = logging.getLogger(__name__)

_TRANSIENT = (aio_pika.exceptions.AMQPError, ConnectionError, OSError, asyncio.TimeoutError)


class RabbitBus:
    """Um canal publicador/consumidor sobre uma conexão robusta."""

    def __init__(self, connection: aio_pika.abc.AbstractRobustConnection, channel: aio_pika.abc.AbstractChannel):
        self._connection = connection
        self._channel = channel
        self._exchanges: dict[str, aio_pika.abc.AbstractExchange] = {}

    @classmethod
    async def connect(
        cls,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        vhost: str = "/",
        prefetch: int = 50,
        name: str = "tankvitals",
        retry_every_s: float = 2.0,
    ) -> "RabbitBus":
        """Conecta, tentando de novo até o broker responder (ele pode subir depois do serviço)."""
        while True:
            try:
                connection = await aio_pika.connect_robust(
                    host=host,
                    port=port,
                    login=user,
                    password=password,
                    virtualhost=vhost,
                    client_properties={"connection_name": name},
                    timeout=10,
                )
                break
            except aio_pika.exceptions.ProbableAuthenticationError:
                # Senha errada não se resolve esperando: melhor parar com a causa no log.
                raise
            except _TRANSIENT as err:
                logger.warning("RabbitMQ indisponível (%s); tentando de novo em %.0f s", err, retry_every_s)
                await asyncio.sleep(retry_every_s)
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        logger.info("Conectado ao RabbitMQ em %s:%s como %s", host, port, user)
        return cls(connection, channel)

    async def _exchange(self, name: str) -> aio_pika.abc.AbstractExchange:
        if name not in self._exchanges:
            self._exchanges[name] = await self._channel.get_exchange(name, ensure=False)
        return self._exchanges[name]

    async def publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        *,
        headers: dict[str, Any] | None = None,
        message_id: str | None = None,
        persistent: bool = False,
        attempts: int = 5,
    ) -> None:
        """Publica e espera a confirmação do broker. Tenta de novo se a conexão estiver caindo."""
        message = aio_pika.Message(
            body=body,
            headers=headers,
            message_id=message_id,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT if persistent else aio_pika.DeliveryMode.NOT_PERSISTENT,
        )
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                target = await self._exchange(exchange)
                await target.publish(message, routing_key=routing_key)
                return
            except _TRANSIENT as err:
                last = err
                logger.warning("Falha ao publicar em %s/%s (tentativa %d/%d): %s", exchange, routing_key, attempt, attempts, err)
                await asyncio.sleep(min(0.5 * attempt, 3.0))
        raise ConnectionError(f"não consegui publicar em {exchange}/{routing_key}: {last}")

    async def consume(
        self,
        queue_name: str,
        callback: Callable[[AbstractIncomingMessage], Awaitable[None]],
    ) -> tuple[aio_pika.abc.AbstractQueue, str]:
        """Começa a consumir a fila. O callback é quem dá `ack` ou `nack`."""
        queue = await self._channel.get_queue(queue_name, ensure=False)
        tag = await queue.consume(callback, no_ack=False)
        return queue, tag

    async def close(self) -> None:
        await self._connection.close()

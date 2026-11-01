"""Adaptador do `rules` com o RabbitMQ: consome `q.rules` e publica comandos e alertas."""

from __future__ import annotations

import asyncio
import logging

from aio_pika.abc import AbstractIncomingMessage

from app.core.pg import Database, build_url
from app.core.rabbit import RabbitBus
from app.core.schema import EnvelopeError, parse_envelope
from app.core.topics import Q_RULES

from .alerts import RangeAlertTracker
from .engine import RuleEngine
from .repo import RulesRepository
from .service import RulesService
from .settings import RulesSettings

logger = logging.getLogger(__name__)


async def run(settings: RulesSettings, stop: asyncio.Event) -> None:
    db = Database(
        build_url(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
        )
    )
    bus = await RabbitBus.connect(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        user=settings.rabbitmq_user,
        password=settings.rabbitmq_password,
        vhost=settings.rabbitmq_vhost,
        prefetch=100,
        name="rules",
    )

    async def publish(exchange, routing_key, body, *, headers=None, message_id=None, persistent=False):
        await bus.publish(exchange, routing_key, body, headers=headers, message_id=message_id, persistent=persistent)

    service = RulesService(
        RulesRepository(db, wait_s=60.0),
        publish,
        engine=RuleEngine(grace_s=settings.rules_command_grace_s),
        tracker=RangeAlertTracker(close_after=settings.rules_alert_close_after),
        reload_every_s=settings.rules_reload_s,
    )

    async def on_message(message: AbstractIncomingMessage) -> None:
        try:
            envelope = parse_envelope(message.body)
            await service.handle_telemetry(message.routing_key or "", envelope.model_dump())
        except EnvelopeError as err:
            logger.warning("Mensagem inválida em q.rules: %s", err)
            await message.nack(requeue=False)  # cai na DLQ
            return
        except Exception:  # noqa: BLE001 - um defeito não pode travar a fila
            logger.exception("Erro ao processar a mensagem %s", message.message_id)
            await message.nack(requeue=False)
            return
        await message.ack()

    try:
        # Só começa a consumir depois de ter as regras: telemetria sem regra carregada seria perdida.
        await service.reload_until_ready()
        queue, tag = await bus.consume(Q_RULES, on_message)
        logger.info("rules no ar, consumindo %s", Q_RULES)
        await service.run_reload_loop(stop)
        await queue.cancel(tag)
    finally:
        await bus.close()
        await asyncio.to_thread(db.dispose)

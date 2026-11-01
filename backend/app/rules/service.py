"""O trabalho do `rules`: telemetria entra, comandos e alertas saem (IOT-07).

Sem aio-pika nem SQLAlchemy aqui: o banco entra por um repositório e o
RabbitMQ por uma função de publicar. O adaptador de verdade está em
`runner.py` e o repositório, em `repo.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.core.envelope import command_payload, dumps, format_ts, make_envelope
from app.core.topics import EX_COMMANDS, EX_EVENTS, TopicError, command_routing_key, parse_telemetry_routing_key

from .alerts import AlertChange, OpenAlert, RangeAlertTracker
from .engine import Decision, RuleEngine

logger = logging.getLogger(__name__)

# exchange, routing key, corpo, e as opções por nome (headers, message_id, persistent)
PublishFn = Callable[..., Awaitable[None]]
# Roda uma função síncrona (o banco) sem travar o laço de eventos.
RunBlocking = Callable[..., Awaitable[Any]]

SEEN_IDS = 4096


class Repository(Protocol):
    def load_rules(self) -> list: ...
    def load_devices(self) -> dict: ...
    def load_fields(self) -> list: ...
    def load_open_alerts(self) -> list[OpenAlert]: ...
    def record_command(self, device: str, command: str, value: Any, *, origin: str, username: str | None, ts: str) -> None: ...
    def open_alert(self, alert: OpenAlert) -> Any: ...
    def escalate_alert(self, alert: OpenAlert) -> None: ...
    def close_alert(self, alert: OpenAlert, ts: str) -> None: ...


class RulesService:
    def __init__(
        self,
        repo: Repository,
        publish: PublishFn,
        *,
        engine: RuleEngine | None = None,
        tracker: RangeAlertTracker | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        run_blocking: RunBlocking = asyncio.to_thread,
        reload_every_s: float = 10.0,
    ):
        self._repo = repo
        self._publish = publish
        self.engine = engine or RuleEngine()
        self.tracker = tracker or RangeAlertTracker()
        self._monotonic = monotonic
        self._run = run_blocking
        self._reload_every_s = reload_every_s
        # Zona e tipo de cada dispositivo, do cadastro (`devices`) ou aprendidos pela routing key.
        self._targets: dict[str, tuple[str, str]] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._restored = False

    # --- recarga do cadastro -------------------------------------------------------

    async def reload(self) -> None:
        """Relê regras, dispositivos e faixas. Se o banco falhar, segue com o que já tinha."""
        try:
            rules = await self._run(self._repo.load_rules)
            devices = await self._run(self._repo.load_devices)
            fields = await self._run(self._repo.load_fields)
            if not self._restored:
                self.tracker.restore(await self._run(self._repo.load_open_alerts))
                self._restored = True
        except Exception:  # noqa: BLE001 - o banco pode estar reiniciando; o serviço continua com o cache
            logger.exception("Não consegui recarregar o cadastro; sigo com o que tenho")
            return
        self.engine.set_rules(rules)
        for device_id, row in devices.items():
            self._targets[device_id] = (row.zone, row.kind)
        for change in self.tracker.set_fields(fields, format_ts()):
            await self._persist_and_publish_alert(change)

    async def reload_until_ready(self, retry_every_s: float = 2.0) -> None:
        """Na partida: tenta até o banco responder com as regras (o `migrate` pode ainda estar rodando)."""
        while True:
            await self.reload()
            if self._restored:
                logger.info("Cadastro carregado: %d regra(s)", len(self.engine.rules))
                return
            await asyncio.sleep(retry_every_s)

    async def run_reload_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._reload_every_s)
            except asyncio.TimeoutError:
                await self.reload()

    # --- a telemetria -----------------------------------------------------------------

    def _duplicate(self, message_id: str) -> bool:
        """O mesmo id duas vezes é descartado (§12). Guarda só os últimos para não crescer sem fim."""
        if message_id in self._seen:
            return True
        self._seen[message_id] = None
        if len(self._seen) > SEEN_IDS:
            self._seen.popitem(last=False)
        return False

    async def handle_telemetry(self, routing_key: str, envelope: dict[str, Any]) -> None:
        """Processa uma telemetria já validada: regra do aerador e alertas de faixa."""
        if envelope.get("type") != "telemetry" or self._duplicate(str(envelope.get("id"))):
            return
        device: str = envelope["device"]
        payload: dict[str, Any] = envelope["payload"]
        ts: str = envelope["ts"]
        now = self._monotonic()

        try:
            parts = parse_telemetry_routing_key(routing_key)
            self._targets.setdefault(device, (parts.zone, parts.kind))
        except TopicError:
            pass

        # O atuador confirma o estado dele na própria telemetria.
        state = payload.get("on")
        if isinstance(state, bool) and device in self.engine.watched_actuators:
            self.engine.observe_state(device, state, now)

        for decision in self.engine.evaluate(device, payload, now):
            await self._send_command(decision, ts)

        for key, value in payload.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            change = self.tracker.update(device, key, float(value), ts)
            if change is not None:
                await self._persist_and_publish_alert(change)

    # --- comando do aerador -----------------------------------------------------------------

    async def _send_command(self, decision: Decision, reading_ts: str) -> None:
        target = self._targets.get(decision.actuator)
        if target is None:
            logger.error("Não sei a zona e o tipo de %s; comando adiado", decision.actuator)
            self.engine.rollback(decision.actuator)
            return
        envelope = make_envelope(
            "command",
            decision.actuator,
            command_payload("on", decision.on, origin="regra", user=None),
        )
        try:
            await self._publish(
                EX_COMMANDS,
                command_routing_key(target[0], target[1], decision.actuator),
                dumps(envelope).encode(),
                message_id=envelope["id"],
                persistent=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Não consegui publicar o comando de %s; tento na próxima leitura", decision.actuator)
            self.engine.rollback(decision.actuator)
            return
        logger.info(
            "Regra %s: %s %s (leitura %s = %s)",
            decision.rule.id,
            decision.actuator,
            "LIGADO" if decision.on else "DESLIGADO",
            decision.rule.field,
            decision.value,
        )
        # O comando já saiu: se o registro falhar, só se perde o histórico, não o controle.
        try:
            await self._run(
                lambda: self._repo.record_command(
                    decision.actuator, "on", decision.on, origin="regra", username=None, ts=envelope["ts"]
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Comando enviado, mas não consegui gravar na tabela commands")

    # --- alertas de faixa ------------------------------------------------------------------------

    async def _persist_and_publish_alert(self, change: AlertChange) -> None:
        alert = change.alert
        try:
            if change.kind == "open":
                alert.db_id = await self._run(self._repo.open_alert, alert)
            elif change.kind == "escalate":
                await self._run(self._repo.escalate_alert, alert)
            else:
                await self._run(self._repo.close_alert, alert, change.ts)
        except Exception:  # noqa: BLE001
            logger.exception("Não consegui gravar o alerta de %s.%s (%s)", alert.device, alert.field, change.kind)
            if change.kind == "open":
                self.tracker.discard(alert.device, alert.field)  # a próxima leitura tenta abrir de novo
            return

        body = {
            "id": alert.public_id,
            "device": alert.device,
            "field": alert.field,
            "level": alert.level,
            "message": alert.message,
            "value": alert.value,
            "opened_at": alert.opened_at,
            "closed_at": change.ts if change.kind == "close" else None,
        }
        envelope = make_envelope("alert", alert.device, body, ts=change.ts)
        try:
            await self._publish(EX_EVENTS, f"alert.{alert.device}", dumps(envelope).encode(), message_id=envelope["id"])
        except Exception:  # noqa: BLE001
            logger.exception("Alerta gravado, mas não consegui publicar no RabbitMQ")
        logger.info("Alerta %s %s: %s", alert.public_id, change.kind, alert.message)

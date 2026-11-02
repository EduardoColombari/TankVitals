"""O que a rota de comando faz, sem FastAPI nem banco nem RabbitMQ (IOT-04).

1. Confere se o dispositivo existe, está ativo e aceita aquele comando.
2. Grava na tabela de comandos e na auditoria: quem, quando e o quê.
3. Publica em `tv.commands`, com a routing key do dispositivo.
4. Responde só `{id, status: "sent"}`. **Não devolve o "novo estado"**: quem
   confirma é a telemetria do atuador, que volta pelo fluxo 1. Por isso a
   tela fica em "aguardando confirmação".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.envelope import command_payload, dumps, make_envelope
from app.core.topics import EX_COMMANDS, command_routing_key

logger = logging.getLogger(__name__)

COMMANDS: tuple[str, ...] = ("on", "inject_fault", "repair")
FAULT_MODES: tuple[str, ...] = ("rolamento", "entupimento", "superaquecimento", "desbalanceamento")


class CommandError(Exception):
    """Erro para devolver ao cliente: o código HTTP e a mensagem (em português)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    kind: str
    zone: str
    active: bool
    commands: tuple[str, ...]


class CommandStore(Protocol):
    """O que a rota precisa do PostgreSQL (implementação em `store.py`)."""

    def get_device(self, device_id: str) -> DeviceInfo | None: ...
    def record_command(self, device_id: str, command: str, value: Any, *, username: str) -> None: ...
    def record_audit(self, *, username: str, target: str, detail: str, ok: bool) -> None: ...


# exchange, routing key, corpo, e as opções por nome (message_id, persistent)
PublishFn = Callable[..., Awaitable[None]]
RunBlocking = Callable[..., Awaitable[Any]]


def validate_command(device: DeviceInfo, command: str, value: Any) -> Any:
    """Confere o comando contra o que o dispositivo aceita e devolve o valor normalizado."""
    if command not in COMMANDS or command not in device.commands:
        accepted = ", ".join(device.commands) or "nenhum"
        raise CommandError(422, f"{device.name} não aceita o comando '{command}'. Aceita: {accepted}.")
    if command == "on":
        # `isinstance(True, int)` é verdade no Python, mas aqui só vale true ou false.
        if not isinstance(value, bool):
            raise CommandError(422, "O comando 'on' pede o valor true (ligar) ou false (desligar).")
        return value
    if command == "inject_fault":
        if value not in FAULT_MODES:
            raise CommandError(422, f"Modo de falha inválido. Use: {', '.join(FAULT_MODES)}.")
        return value
    return None  # repair não leva valor


def describe(command: str, value: Any) -> str:
    """O texto do detalhe na auditoria, no formato que a tela já mostra."""
    if command == "on":
        return f"on = {'true' if value else 'false'}"
    if command == "inject_fault":
        return f"inject_fault = {value}"
    return command


class CommandService:
    def __init__(self, store: CommandStore, publish: PublishFn, *, run_blocking: RunBlocking = asyncio.to_thread):
        self._store = store
        self._publish = publish
        self._run = run_blocking

    async def send(self, *, username: str, device_id: str, command: str, value: Any = None) -> dict[str, str]:
        device = await self._run(self._store.get_device, device_id)
        if device is None:
            await self._audit(username, device_id, f"{command} — dispositivo não existe", False)
            raise CommandError(404, f"Dispositivo '{device_id}' não encontrado.")
        if not device.active:
            await self._audit(username, device_id, f"{command} — dispositivo desativado", False)
            raise CommandError(409, f"{device.name} está desativado. Reative o dispositivo para comandá-lo.")
        try:
            value = validate_command(device, command, value)
        except CommandError as err:
            await self._audit(username, device_id, f"{command} — recusado: {err.detail}", False)
            raise

        # Grava antes de publicar: se a API cair no meio, fica o registro de quem pediu o quê.
        await self._run(lambda: self._store.record_command(device_id, command, value, username=username))

        envelope = make_envelope(
            "command",
            device_id,
            command_payload(command, value, origin="web", user=username),
        )
        try:
            await self._publish(
                EX_COMMANDS,
                command_routing_key(device.zone, device.kind, device_id),
                dumps(envelope).encode(),
                message_id=envelope["id"],
                persistent=True,
            )
        except Exception:  # noqa: BLE001 - qualquer falha do barramento vira 503
            logger.exception("Não consegui publicar o comando %s de %s", command, device_id)
            await self._audit(username, device_id, f"{describe(command, value)} — não chegou ao barramento", False)
            raise CommandError(503, "Não consegui enviar o comando agora: o barramento de mensagens está indisponível.")

        await self._audit(username, device_id, describe(command, value), True)
        return {"id": envelope["id"], "status": "sent"}

    async def _audit(self, username: str, target: str, detail: str, ok: bool) -> None:
        # A auditoria nunca derruba a resposta: se falhar, fica no log.
        try:
            await self._run(lambda: self._store.record_audit(username=username, target=target, detail=detail, ok=ok))
        except Exception:  # noqa: BLE001
            logger.exception("Não consegui gravar a auditoria (%s, %s)", target, detail)

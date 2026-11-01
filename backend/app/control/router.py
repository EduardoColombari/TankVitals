"""A rota `POST /api/devices/{id}/command` (IOT-04), pronta para a API incluir.

Só operador e admin comandam: quem barra é a API (`require_role`, BACK-03); a
tela só esconde o botão. Como o login e a conexão com o RabbitMQ são da BACK,
a rota recebe os dois de fora. Exemplo de como a API liga tudo:

    from app.control.router import create_command_router
    from app.control.service import CommandService
    from app.control.store import PostgresCommandStore

    service = CommandService(PostgresCommandStore(db), bus.publish)
    app.include_router(
        create_command_router(service, require_operator=require_role("operator")),
        prefix="/api",
    )

`require_operator` é uma dependência do FastAPI que devolve o usuário logado
(com `.username`, ou um texto, ou um dicionário com `username`/`sub`) e levanta
401/403 por conta própria.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .service import CommandError, CommandService


class CommandRequest(BaseModel):
    """Corpo: `{"command": "on", "value": true}`, `{"command": "inject_fault", "value": "rolamento"}` ou `{"command": "repair"}`."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=32)
    # Estrito: o número 1 não vale como true, e "true" (texto) não vale como ligar.
    value: StrictBool | StrictStr | None = None


class CommandAck(BaseModel):
    """A API só diz que mandou. O estado novo chega pela telemetria do dispositivo."""

    id: str
    status: str = "sent"


def username_of(actor: Any) -> str:
    """Tira o nome do usuário do que a dependência de login devolveu."""
    if isinstance(actor, str):
        return actor
    if isinstance(actor, dict):
        name = actor.get("username") or actor.get("sub")
    else:
        name = getattr(actor, "username", None) or getattr(actor, "sub", None)
    if not name:
        raise RuntimeError("a dependência de login precisa devolver o usuário (username)")
    return str(name)


def create_command_router(service: CommandService, *, require_operator: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["controle"])

    @router.post("/devices/{device_id}/command", response_model=CommandAck)
    async def send_command(device_id: str, body: CommandRequest, actor: Any = Depends(require_operator)) -> CommandAck:
        try:
            ack = await service.send(
                username=username_of(actor), device_id=device_id, command=body.command, value=body.value
            )
        except CommandError as err:
            raise HTTPException(status_code=err.status, detail=err.detail) from err
        return CommandAck(**ack)

    return router

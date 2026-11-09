import json
from fastapi import APIRouter, Depends, HTTPException, Request
from app.core.auth import require_role
from app.control.router import CommandRequest
from app.control.service import CommandService, CommandError
from app.control.store import PostgresCommandStore

router = APIRouter(prefix='/api')

@router.post('/devices/{device_id}/command')
async def command(device_id: str, body: CommandRequest, request: Request,
                  actor=Depends(require_role('operator'))):
    database = getattr(request.app.state, 'database', None)
    bus = getattr(request.app.state, 'bus', None)
    if database is None or bus is None:
        raise HTTPException(503, 'Serviço de comandos indisponível')
    async def publish(exchange, routing_key, body, **kwargs):
        await bus.publish(exchange, routing_key, json.loads(body))
    service = CommandService(PostgresCommandStore(database), publish)
    try:
        return await service.send(username=actor['sub'], device_id=device_id,
                                  command=body.command, value=body.value)
    except CommandError as exc:
        raise HTTPException(exc.status, exc.detail) from exc

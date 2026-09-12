"""Rotas REST e WebSocket: ARQUITETURA §6."""
import asyncio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from app.alerts import RANGES
from app.influx_repo import validate_query
from app.presentation import present_reading, utc_time

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    db = request.app.state.repo.ping()
    mqtt = request.app.state.ingestor.client.is_connected()
    payload = {"status": "ok" if db and mqtt else "degraded", "influxdb": db, "mqtt": mqtt}
    if not db or not mqtt:
        payload["detail"] = "Um ou mais serviços estão indisponíveis"
    return JSONResponse(payload, status_code=200 if db and mqtt else 503)


@router.get("/thresholds")
def thresholds() -> dict:
    return RANGES


@router.get("/tanks")
def list_tanks(request: Request) -> list[dict]:
    ingestor = request.app.state.ingestor
    tanks = {row["tank_id"]: dict(row) for row in request.app.state.repo.list_tanks()}
    for tank_id, reading in list(ingestor.last_reading.items()):
        if tank_id not in tanks or utc_time(reading["time"]) > utc_time(tanks[tank_id]["last_seen"]):
            tanks[tank_id] = {"tank_id": tank_id, "last_seen": reading["time"]}
    for tank_id in list(ingestor.online):
        tanks.setdefault(tank_id, {"tank_id": tank_id, "last_seen": None})
    return [{**row, "online": ingestor.online.get(tank_id, False)}
            for tank_id, row in sorted(tanks.items())]


@router.get("/readings/latest")
def latest(request: Request, tank_id: str = "tanque-01") -> dict:
    validate_query(tank_id)
    ingestor = request.app.state.ingestor
    reading = ingestor.last_reading.get(tank_id)
    if reading is None:
        reading = request.app.state.repo.get_latest(tank_id)
    if reading is None:
        raise HTTPException(404, "Tanque sem leituras")
    return present_reading(reading, ingestor.online.get(tank_id, False))


@router.get("/readings/history")
def history(request: Request, tank_id: str = "tanque-01", range: str = "6h",
            window: str | None = None, metrics: str | None = None) -> dict:
    selected = [m.strip() for m in metrics.split(",")] if metrics is not None else None
    validate_query(tank_id, range, window, selected)
    return request.app.state.repo.get_history(tank_id, range, window, selected)


@router.get("/stats")
def stats(request: Request, tank_id: str = "tanque-01", range: str = "24h") -> dict:
    validate_query(tank_id, range)
    return request.app.state.repo.get_stats(tank_id, range)


class ConnectionManager:
    """Filtra por tanque e isola clientes lentos com filas limitadas."""
    def __init__(self) -> None:
        self.active: dict[WebSocket, tuple[str, asyncio.Queue]] = {}
        self.senders: dict[WebSocket, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, tank_id: str) -> None:
        await websocket.accept()
        queue = asyncio.Queue(maxsize=100)
        self.active[websocket] = (tank_id, queue)
        self.senders[websocket] = asyncio.create_task(self._send(websocket, queue))

    async def _send(self, websocket: WebSocket, queue: asyncio.Queue) -> None:
        try:
            while True:
                await asyncio.wait_for(websocket.send_json(await queue.get()), timeout=5)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.active.pop(websocket, None)
            self.senders.pop(websocket, None)
            try:
                await websocket.close()
            except Exception:
                pass

    async def disconnect(self, websocket: WebSocket) -> None:
        self.active.pop(websocket, None)
        task = self.senders.pop(websocket, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def broadcast(self, event: dict) -> None:
        for websocket, (tank_id, queue) in list(self.active.items()):
            if tank_id == event["payload"]["tank_id"]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    await self.disconnect(websocket)

    async def close(self) -> None:
        for websocket in list(self.active):
            await self.disconnect(websocket)


async def live(websocket: WebSocket, tank_id: str = "tanque-01") -> None:
    try:
        validate_query(tank_id)
    except ValueError:
        await websocket.close(code=1008)
        return
    manager = websocket.app.state.manager
    await manager.connect(websocket, tank_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)

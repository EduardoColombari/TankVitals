"""API e ingestor no mesmo processo: uvicorn app.main:app --reload."""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api import ConnectionManager, live, router
from app.config import settings
from app.influx_repo import InfluxRepository, RepositoryUnavailable
from app.mqtt_ingestor import MqttIngestor
from app.presentation import present_reading
from app.messaging import MessagingMonitor, router as messaging_router
from app.ai_api import current_user
from fastapi.security import HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)


def create_app(repo_factory=InfluxRepository, ingestor_factory=MqttIngestor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        token_file = os.getenv("INFLUX_TOKEN_FILE")
        if token_file:
            try:
                token = open(token_file, encoding="utf-8").read().strip()
            except OSError as exc:
                raise RuntimeError("Could not read the API-scoped InfluxDB token") from exc
            if not token:
                raise RuntimeError("The API-scoped InfluxDB token file is empty")
            settings.influx_token = token
            os.environ["INFLUX_TOKEN"] = token
        loop = asyncio.get_running_loop()
        manager = ConnectionManager()
        repo = repo_factory()
        closing = False

        def completed(future):
            if not future.cancelled() and future.exception() is not None:
                logger.error("Falha no envio WebSocket: %s", future.exception())

        def schedule(event):
            if not closing:
                future = asyncio.run_coroutine_threadsafe(manager.broadcast(event), loop)
                future.add_done_callback(completed)

        def on_reading(reading):
            schedule({"type": "reading", "payload": present_reading(
                reading, ingestor.online.get(reading["tank_id"], False))})

        def on_status(status):
            schedule({"type": "status", "payload": status})

        ingestor = ingestor_factory(on_reading=on_reading, on_status=on_status, repository=repo)
        app.state.repo, app.state.ingestor, app.state.manager = repo, ingestor, manager
        ai_bus, ai_store, ai_dispatcher = None, None, None
        messaging_stop, messaging_task = None, None
        try:
            if os.environ.get("RABBITMQ_URL"):
                async def forward_rabbit_event(routing_key: str, body: bytes) -> None:
                    try:
                        envelope = json.loads(body.decode("utf-8"))
                        if not isinstance(envelope, dict) or envelope.get("type") not in {"telemetry", "insight"}:
                            return
                        if envelope["type"] == "telemetry":
                            parts = routing_key.split(".")
                            if len(parts) != 4 or parts[0] != "telemetry":
                                return
                            tank_id = parts[1]
                        else:
                            tank_id = "tanque-01"
                        payload = envelope.get("payload")
                        if not isinstance(payload, dict):
                            return
                        await manager.broadcast({**envelope, "payload": {**payload, "tank_id": tank_id}})
                    except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
                        logger.warning("Invalid q.ws message was stored but not sent to WebSocket")

                monitor = MessagingMonitor(
                    os.getenv("MESSAGING_DB_PATH", "/var/lib/tankvitals/messaging.sqlite"),
                    event_handler=forward_rabbit_event,
                )
                app.state.messaging_monitor = monitor
                messaging_stop = asyncio.Event()
                messaging_task = asyncio.create_task(monitor.consume_qws(messaging_stop))
            if os.environ.get("AI_DATABASE_URL"):
                from app.ai_store import AIStore
                from app.ai_transport import RabbitBus
                from app.ai_api import dispatch_jobs
                ai_store = await asyncio.to_thread(AIStore, os.environ["AI_DATABASE_URL"])
                app.state.ai_store = ai_store
                ai_bus = RabbitBus()
                await ai_bus.connect(os.environ["RABBITMQ_URL"])
                ai_dispatcher = asyncio.create_task(dispatch_jobs(ai_store, ai_bus))
            await asyncio.to_thread(ingestor.start)
            yield
        finally:
            closing = True
            await asyncio.to_thread(ingestor.stop)
            await manager.close()
            await asyncio.to_thread(repo.close)
            if ai_dispatcher:
                ai_dispatcher.cancel()
                await asyncio.gather(ai_dispatcher, return_exceptions=True)
            if ai_bus and hasattr(ai_bus, "connection"):
                await ai_bus.close()
            if ai_store:
                await asyncio.to_thread(ai_store.close)
            if messaging_stop:
                messaging_stop.set()
            if messaging_task:
                await asyncio.gather(messaging_task, return_exceptions=True)

    app = FastAPI(title="TankVitals API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def require_api_session(request: Request, call_next):
        if (os.getenv("JWT_SECRET") and request.url.path.startswith("/api/")
                and request.url.path not in {"/api/health", "/api/auth/login"}):
            value = request.headers.get("authorization", "")
            scheme, _, token = value.partition(" ")
            try:
                if scheme.lower() != "bearer" or not token:
                    raise HTTPException(401, "Login necessário")
                current_user(HTTPAuthorizationCredentials(scheme=scheme, credentials=token))
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)
    app.add_middleware(CORSMiddleware,
                       allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(RepositoryUnavailable)
    async def unavailable(request: Request, exc: RepositoryUnavailable):
        return JSONResponse({"detail": "InfluxDB indisponível"}, status_code=503)

    @app.exception_handler(ValueError)
    async def bad_parameter(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Parâmetros inválidos"}, status_code=400)

    app.include_router(router)
    app.include_router(messaging_router)
    from app.ai_api import router as ai_router
    app.include_router(ai_router)
    app.add_api_websocket_route("/ws/live", live)
    return app


app = create_app()

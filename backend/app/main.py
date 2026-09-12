"""API e ingestor no mesmo processo: uvicorn app.main:app --reload."""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api import ConnectionManager, live, router
from app.config import settings
from app.influx_repo import InfluxRepository, RepositoryUnavailable
from app.mqtt_ingestor import MqttIngestor
from app.presentation import present_reading

logger = logging.getLogger(__name__)


def create_app(repo_factory=InfluxRepository, ingestor_factory=MqttIngestor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
        try:
            await asyncio.to_thread(ingestor.start)
            yield
        finally:
            closing = True
            await asyncio.to_thread(ingestor.stop)
            await manager.close()
            await asyncio.to_thread(repo.close)

    app = FastAPI(title="TankVitals API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
                       allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["GET"], allow_headers=["*"])

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
    app.add_api_websocket_route("/ws/live", live)
    return app


app = create_app()

"""Rotas IA; autenticação JWT compartilhada com BACK por JWT_SECRET."""
import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.datagen import DatasetConfig
from app.core.physics import FAULT_MODES

router = APIRouter(prefix="/api", tags=["IA"])
bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise HTTPException(503, "Autenticação da IA não configurada")
    if credentials is None:
        raise HTTPException(401, "Login necessário")
    import jwt
    try:
        user = jwt.decode(credentials.credentials, secret, algorithms=["HS256"],
                          options={"require": ["exp", "sub", "role"]})
        if user["role"] not in ("viewer", "operator", "admin"):
            raise ValueError("papel inválido")
        return user
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, "Token inválido ou vencido") from None


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Apenas administradores podem iniciar jobs")
    return user


def store(request: Request):
    result = getattr(request.app.state, "ai_store", None)
    if result is None:
        raise HTTPException(503, "Configure AI_DATABASE_URL para habilitar a IA")
    return result


class DatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    num_pumps: int = Field(100, ge=1, strict=True)
    days: int = Field(7, ge=1, strict=True)
    sensor_count: int = Field(4, ge=4, le=7, strict=True)
    failure_fraction: float = Field(0.3, ge=0, le=1)
    failure_modes: list[str] = Field(default_factory=lambda: list(FAULT_MODES))
    noise_scale: float = Field(1, ge=0, le=10)
    missing_fraction: float = Field(0, ge=0, le=1)
    absurd_fraction: float = Field(0, ge=0, le=1)
    seed: int = Field(42, ge=0, strict=True)
    block_minutes: int = Field(60, ge=1, le=1440, strict=True)

    @model_validator(mode="after")
    def volume(self):
        DatasetConfig(**self.model_dump()).validate()
        return self


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=80)
    window_size: int = Field(30, ge=2, le=240, strict=True)
    sample_every: int = Field(10, ge=1, le=1440, strict=True)
    seed: int = Field(42, ge=0, strict=True)


@router.post("/lab/jobs", status_code=202)
def generate(body: DatasetRequest, repo=Depends(store), user=Depends(admin)):
    return repo.create_job("datagen", body.model_dump(), user["sub"])


@router.get("/lab/jobs")
def jobs(repo=Depends(store), user=Depends(current_user)):
    return repo.list("job")


@router.get("/lab/datasets")
def datasets(repo=Depends(store), user=Depends(current_user)):
    return [{k: v for k, v in row.items() if k != "path"} for row in repo.list("dataset")]


@router.post("/models/train", status_code=202)
def train(body: TrainRequest, repo=Depends(store), user=Depends(admin)):
    if repo.get(body.dataset_id, "dataset") is None:
        raise HTTPException(404, "Dataset não encontrado")
    return repo.create_job("train", body.model_dump(), user["sub"])


@router.get("/models")
def models(repo=Depends(store), user=Depends(current_user)):
    active = repo.get("active-model") or {}
    return [{**{k: v for k, v in row.items() if k != "artifact_path"},
             "active": row["version"] == active.get("version")} for row in repo.list("model")]


@router.get("/insights/history")
def history(request: Request, range: str = "6h", device: str | None = None,
            user=Depends(current_user)):
    from app.ai_influx import insight_history
    return insight_history(range, device)


async def dispatch_jobs(repo, bus):
    """Publica pedidos duráveis; se o broker cair, tenta de novo sem perder o job."""
    import logging
    from app.ai_transport import envelope
    while True:
        try:
            for job in await asyncio.to_thread(repo.list, "job"):
                if job["status"] == "queued" and not job.get("published"):
                    await bus.publish("tv.jobs", job["kind"], envelope("job_progress", "api",
                                      {"job_id": job["id"]}, message_id=job["id"]))
                    # Não regravar um snapshot antigo por cima do progresso do worker.
                    await asyncio.to_thread(repo.mark_published, job["id"])
        except Exception:
            logging.getLogger(__name__).exception("Falha na publicação dos jobs; nova tentativa em 2 s")
        await asyncio.sleep(2)

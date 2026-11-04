import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import joblib
import jwt
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_api import router
from app.ai_jobs import JobRunner
from app.ai_store import AIStore
from app.ai_transport import envelope
from app.inference import InferenceService
from app.core.physics import PUMP_FIELDS, advance_block, advance_population, create_population, inject_fault
from app.core.datagen import _labels


class Bus:
    def __init__(self):
        self.events = []
        self.fail = False

    async def publish(self, exchange, key, message):
        if self.fail:
            raise ConnectionError("broker indisponível")
        self.events.append((exchange, key, message))


class Constant:
    def __init__(self, value):
        self.value = value

    def predict(self, features):
        return np.full(len(features), self.value)


@pytest.fixture
def store(tmp_path):
    repo = AIStore(f"sqlite:///{tmp_path / 'ai.db'}")
    yield repo
    repo.close()


def test_api_requires_real_admin_token_and_validates_before_queueing(store, monkeypatch):
    secret = "test-only-" + "a" * 40
    monkeypatch.setenv("JWT_SECRET", secret)
    app = FastAPI()
    app.state.ai_store = store
    app.include_router(router)
    def headers(role):
        token = jwt.encode({"sub": "tester", "role": role,
                            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, secret, algorithm="HS256")
        return {"Authorization": "Bearer " + token}
    with TestClient(app) as client:
        assert client.post("/api/lab/jobs", json={}).status_code == 401
        assert client.post("/api/lab/jobs", json={}, headers=headers("viewer")).status_code == 403
        assert client.post("/api/lab/jobs", json={"num_pumps": 100000}, headers=headers("admin")).status_code == 422
        assert store.list("job") == []
        response = client.post("/api/lab/jobs", json={}, headers=headers("admin"))
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert store.list("audit")[0]["user"] == "tester"
        assert client.post("/api/models/train", json={"dataset_id": "../../bad"}, headers=headers("admin")).status_code == 404


def test_worker_generates_off_event_loop_and_is_idempotent(store, tmp_path):
    job = store.create_job("datagen", {"num_pumps": 2, "days": 1}, "admin")
    bus = Bus()
    ticks = []
    async def scenario():
        runner = JobRunner(store, bus, tmp_path)
        async def heartbeat():
            while store.get(job["id"])["status"] != "done":
                ticks.append(True)
                await asyncio.sleep(0.001)
        await asyncio.wait_for(asyncio.gather(runner.run(job["id"]), heartbeat()), 30)
        await runner.run(job["id"])
    asyncio.run(scenario())
    assert len(ticks) > 1
    assert len(store.list("dataset")) == 1
    assert store.get(job["id"])["progress"] == 1
    assert bus.events[-1][2]["payload"]["status"] == "done"


def test_inference_recovers_alert_state_and_pending_events(store, tmp_path):
    bus = Bus()
    version = "v-" + str(uuid4())
    directory = tmp_path / "models" / version
    directory.mkdir(parents=True)
    joblib.dump({"health_true": Constant(20), "rul_h_true": Constant(12)}, directory / "models.joblib")
    store.put("active", {"version": version}, "active-model")
    start = datetime.now(timezone.utc)
    def reading(index):
        return envelope("telemetry", "bomba-rec-01", {field: 1 for field in PUMP_FIELDS},
                        ts=(start + timedelta(minutes=index)).isoformat())
    async def scenario():
        service = InferenceService(store, bus, tmp_path)
        for index in range(32):
            await service.handle(reading(index))
        assert len(store.list("alert")) == 1
        assert store.list("alert")[0]["closed_at"] is None
        # Simula queda depois de persistir o resultado, antes da publicação.
        bus.fail = True
        message = reading(32)
        with pytest.raises(ConnectionError):
            await service.handle(message)
        bus.fail = False
        recovered = InferenceService(store, bus, tmp_path)
        await recovered.handle(message)
        assert len([event for event in bus.events if event[2]["id"] == message["id"] + "-insight"]) == 1
        recovered.engine._models = {"health_true": Constant(90), "rul_h_true": Constant(168)}
        for index in range(33, 43):
            await recovered.handle(reading(index))
        assert store.list("alert")[0]["closed_at"] is not None
    asyncio.run(scenario())


def test_rul_matches_actual_nonlinear_failure_and_block_matches_single_steps():
    population = create_population(10, failure_fraction=1, seed=19)
    inject_fault(population, "rolamento")
    _, rul = _labels(population)
    remaining = rul * 60
    for minute in range(1, int(remaining.max()) + 2):
        advance_population(population, noise_scale=0)
        np.testing.assert_array_equal(population.wear >= 1 - 1e-12, remaining <= minute)
    first = create_population(10, failure_fraction=1, seed=12)
    second = create_population(10, failure_fraction=1, seed=12)
    block, _ = advance_block(first, 60, noise_scale=0)
    rows = [advance_population(second, noise_scale=0) for _ in range(60)]
    for field in PUMP_FIELDS:
        np.testing.assert_allclose(block[field], np.concatenate([row[field] for row in rows]), atol=1e-10)

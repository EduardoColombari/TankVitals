"""Execução testável dos jobs, sem bloquear o event loop do RabbitMQ."""
import asyncio
import json
from pathlib import Path

from app.ai_store import now
from app.ai_transport import envelope
from app.core.datagen import DatasetConfig, generate_dataset
from app.core.training import TrainingConfig, train_models


class JobRunner:
    def __init__(self, store, bus, root, sample_writer=None):
        self.store, self.bus, self.root = store, bus, Path(root)
        self.sample_writer = sample_writer
        self.lock = asyncio.Lock()

    async def progress(self, job, done, total, message=None):
        job.update(progress=done / total, message=message or f"{done} de {total}")
        await asyncio.to_thread(self.store.put, "job", job, job["id"])
        await self.bus.publish("tv.events", f"job.{job['kind']}", envelope("job_progress", "worker", {
            "job_id": job["id"], **{key: job[key] for key in ("kind", "status", "progress", "message")}}))

    async def run(self, job_id):
        async with self.lock:
            job = await asyncio.to_thread(self.store.get, job_id, "job")
            if job is None:
                raise ValueError("job inexistente")
            if job["status"] in ("done", "failed"):
                return
            loop = asyncio.get_running_loop()
            def report(done, total):
                # Espera a publicação: não acumula milhares de futures na memória.
                asyncio.run_coroutine_threadsafe(self.progress(job, done, total), loop).result()
            job["status"] = "running"
            await self.progress(job, 0, 1, "Iniciando")
            try:
                if job["kind"] == "datagen":
                    config = DatasetConfig(**job["params"])
                    path = self.root / "datasets" / f"{job_id}.parquet"
                    # Uma reentrega após a gravação reaproveita o arquivo atômico.
                    if not path.exists():
                        await asyncio.to_thread(generate_dataset, config, path, report)
                    dataset = {"created_at": job["created_at"], "rows": config.total_rows,
                               "params": job["params"], "path": str(path), "bytes": path.stat().st_size}
                    await asyncio.to_thread(self.store.put, "dataset", dataset, job_id + "-dataset")
                    if self.sample_writer:
                        await asyncio.to_thread(self.sample_writer, path, job_id)
                    job["result"] = {"dataset_id": job_id + "-dataset"}
                elif job["kind"] == "train":
                    params = dict(job["params"])
                    dataset = await asyncio.to_thread(self.store.get, params.pop("dataset_id"), "dataset")
                    if dataset is None:
                        raise ValueError("dataset inexistente")
                    version = "v-" + job_id
                    saved = await asyncio.to_thread(self.store.get, version, "model")
                    if saved:
                        result = saved
                    else:
                        result_path = self.root / "models" / version / "result.json"
                        if result_path.exists():
                            result = json.loads(await asyncio.to_thread(result_path.read_text, encoding="utf-8"))
                        else:
                            result = await asyncio.to_thread(train_models, dataset["path"], self.root / "models",
                                                             version, TrainingConfig(**params), report)
                        result["created_at"] = now()
                        await asyncio.to_thread(self.store.put, "model", result, version)
                    if result["accepted"]:
                        await asyncio.to_thread(self.store.put, "active", {"version": version}, "active-model")
                        await self.bus.publish("tv.events", "model.updated", envelope("model_updated", "worker", {
                            "model": "health,rul", "version": version}))
                    job["result"] = {"version": version, "accepted": result["accepted"]}
                else:
                    raise ValueError("tipo de job desconhecido")
                job["status"] = "done"
                await self.progress(job, 1, 1, "Concluído")
            except Exception:
                job["status"] = "failed"
                await self.progress(job, job["progress"], 1, "Falha no processamento; consulte o log do worker")
                raise

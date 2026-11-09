"""python -m app.worker — consumidor único das filas de geração e treino."""
import asyncio
import json
import logging
import os

from app.ai_jobs import JobRunner
from app.ai_store import AIStore
from app.ai_transport import RabbitBus


async def main():
    from app.ai_influx import write_lab_sample
    store = AIStore(os.environ["AI_DATABASE_URL"])
    bus = RabbitBus()
    await bus.connect(os.environ["RABBITMQ_URL"])
    runner = JobRunner(store, bus, os.environ.get("AI_DATA_DIR", "data/ai"), write_lab_sample)
    async def consume(message):
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body)
                await runner.run(payload["payload"]["job_id"])
            except Exception:
                logging.getLogger(__name__).exception("Job rejeitado para DLQ")
                raise
    try:
        for name in ("q.jobs.datagen", "q.jobs.train"):
            queue = await bus.channel.get_queue(name)
            await queue.consume(consume)
        await asyncio.Future()
    finally:
        await bus.close()
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

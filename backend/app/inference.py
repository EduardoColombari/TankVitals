"""python -m app.inference — inferência, recarga e histerese persistente."""
import asyncio
import json
import logging
import os
import re
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.ai_store import AIStore
from app.ai_transport import RabbitBus, envelope
from app.core.alert_hysteresis import AlertState, update_alert_state
from app.core.inference import InferenceEngine


class InferenceService:
    def __init__(self, store, bus, root):
        self.store, self.bus, self.root = store, bus, Path(root)
        self.engine = InferenceEngine()

    async def reload(self):
        active = await asyncio.to_thread(self.store.get, "active-model")
        if active and active["version"] != self.engine.model_version:
            version = active["version"]
            if not re.fullmatch(r"v-[a-f0-9-]{36}", version):
                raise ValueError("versão inválida")
            await asyncio.to_thread(self.engine.load_version, self.root / "models" / version, version)

    async def flush(self, state):
        for exchange, key, message in state.get("pending", []):
            if message["type"] == "alert":
                await asyncio.to_thread(self.store.put, "alert", message["payload"], message["payload"]["id"])
            await self.bus.publish(exchange, key, message)
        if state.get("pending"):
            state["pending"] = []
            await asyncio.to_thread(self.store.put, "inference_state", state, state["id"])

    async def handle(self, message):
        await self.reload()
        if message["type"] == "model_updated":
            return
        if message["type"] != "telemetry":
            raise ValueError("mensagem não suportada na inferência")
        device = message["device"]
        if not re.fullmatch(r"[a-z0-9-]{1,60}", device):
            raise ValueError("dispositivo inválido")
        timestamp = datetime.fromisoformat(message["ts"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("timestamp precisa de timezone")
        state_id = "inference-" + device
        state = await asyncio.to_thread(self.store.get, state_id) or {
            "id": state_id, "history": [], "alert_state": asdict(AlertState()), "pending": []}
        await self.flush(state)
        if state.get("last_ts") and timestamp <= datetime.fromisoformat(state["last_ts"]):
            return
        history = state["history"] if state.get("window_size", self.engine.window_size) == self.engine.window_size else []
        self.engine._histories[device] = deque(history, maxlen=self.engine.window_size)
        if message["payload"].get("running") is False:
            self.engine._histories.pop(device, None)
            prediction = None
            # Bomba parada não é prova de reparo: mantém o alerta, zera sequências.
            state["alert_state"] = asdict(AlertState(is_open=state["alert_state"]["is_open"]))
        else:
            try:
                prediction = await asyncio.to_thread(self.engine.update, device, message["payload"])
            except ValueError:
                # Uma leitura inválida quebra a janela, assim como no treino.
                state.update(history=[], last_ts=timestamp.isoformat(), pending=[],
                             alert_state=asdict(AlertState(is_open=state["alert_state"]["is_open"])))
                await asyncio.to_thread(self.store.put, "inference_state", state, state_id)
                raise
        pending = []
        if prediction:
            payload = {key: value for key, value in prediction.items() if key != "device"}
            pending.append(("tv.insights", f"insight.{device}", envelope("insight", device, payload,
                           message_id=message["id"] + "-insight", ts=message["ts"])))
            alert_state, transition = update_alert_state(AlertState(**state["alert_state"]),
                                                         prediction["health"], prediction["rul_h"])
            state["alert_state"] = asdict(alert_state)
            if transition:
                if transition == "open":
                    state["alert"] = {"id": message["id"] + "-alert", "device": device,
                                      "field": None, "value": None, "level": "critico",
                                      "message": "Saúde baixa ou vida útil inferior a 24 h. Inspecione a bomba e programe manutenção.",
                                      "opened_at": message["ts"], "closed_at": None}
                else:
                    state["alert"]["closed_at"] = message["ts"]
                pending.append(("tv.events", f"alert.{device}", envelope("alert", device, dict(state["alert"]),
                               message_id=message["id"] + "-alert-event", ts=message["ts"])))
        state.update(last_ts=timestamp.isoformat(), history=list(self.engine._histories.pop(device, [])),
                     window_size=self.engine.window_size, pending=pending)
        # Estado e eventos ficam no mesmo registro atômico antes de publicar.
        await asyncio.to_thread(self.store.put, "inference_state", state, state_id)
        await self.flush(state)


async def main():
    store = AIStore(os.environ["AI_DATABASE_URL"])
    bus = RabbitBus()
    await bus.connect(os.environ["RABBITMQ_URL"])
    service = InferenceService(store, bus, os.environ.get("AI_DATA_DIR", "data/ai"))
    try:
        for state in await asyncio.to_thread(store.list, "inference_state"):
            await service.flush(state)
        await service.reload()
        queue = await bus.channel.get_queue("q.inference")
        async with queue.iterator() as messages:
            async for message in messages:
                async with message.process(requeue=False):
                    await service.handle(json.loads(message.body))
    finally:
        await bus.close()
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

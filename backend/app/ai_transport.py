"""Transporte IA: usa somente a topologia previamente criada por PLAT."""
import json
from uuid import uuid4

from app.ai_store import now


def envelope(kind, device, payload, *, message_id=None, ts=None):
    return {"id": message_id or str(uuid4()), "ts": ts or now(), "type": kind,
            "device": device, "payload": payload}


class RabbitBus:
    async def connect(self, url):
        import aio_pika
        self.connection = await aio_pika.connect_robust(url)
        self.channel = await self.connection.channel(publisher_confirms=True)
        await self.channel.set_qos(prefetch_count=1)

    async def publish(self, exchange, routing_key, message):
        import aio_pika
        target = await self.channel.get_exchange(exchange)
        await target.publish(aio_pika.Message(
            json.dumps(message, allow_nan=False).encode(), content_type="application/json",
            message_id=message["id"], delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=routing_key, mandatory=True)

    async def close(self):
        await self.connection.close()

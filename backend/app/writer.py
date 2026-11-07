"""PLAT-06: batch RabbitMQ messages from q.writer into InfluxDB."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote


logger = logging.getLogger("tankvitals.writer")
RETRY_HEADER = "x-plat-write-retries"
ORIGINAL_ROUTING_KEY_HEADER = "x-plat-original-routing-key"
RETRY_EXCHANGE = "tv.writer.retry"
FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_BATCH_SIZE = 200
MAX_WRITE_RETRIES = 2
RETRY_BACKOFF_S = (1.0, 2.0)
FLUSH_INTERVAL_S = 0.25


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Envelope ts must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Envelope ts is not valid ISO 8601") from exc
    if timestamp.tzinfo is None:
        raise ValueError("Envelope ts must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _influx_token() -> str:
    """Read the Writer-scoped token from a mounted secret file when configured."""
    token_file = os.getenv("INFLUX_TOKEN_FILE")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Could not read InfluxDB token file") from exc
    else:
        token = os.getenv("INFLUX_TOKEN", "").strip()
        if token:
            logger.warning("INFLUX_TOKEN_FILE is unset; using INFLUX_TOKEN environment variable")
    if not token:
        raise RuntimeError("Set INFLUX_TOKEN_FILE to a non-empty Writer-scoped token file")
    return token


def to_influx_record(body: bytes, routing_key: str) -> dict[str, Any]:
    """Validate a telemetry/insight envelope and return an Influx record."""
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Message is not valid UTF-8 JSON") from exc
    if not isinstance(envelope, dict):
        raise ValueError("Message envelope must be a JSON object")

    try:
        uuid.UUID(str(envelope["id"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Envelope id must be a UUID") from exc
    if not isinstance(envelope.get("device"), str) or not envelope["device"]:
        raise ValueError("Envelope device is required")
    timestamp = _parse_timestamp(envelope.get("ts"))
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Envelope payload must be a non-empty object")

    event_type = envelope.get("type")
    if event_type == "telemetry":
        parts = routing_key.split(".")
        if len(parts) != 4 or parts[0] != "telemetry":
            raise ValueError("Telemetry routing key must be telemetry.<zone>.<kind>.<device>")
        _, zone, kind, route_device = parts
        if route_device != envelope["device"]:
            raise ValueError("Envelope device does not match telemetry routing key")
        tags = {"device": route_device, "zone": zone, "kind": kind}
        measurement = "telemetry"
        raw_fields = payload
    elif event_type == "insight":
        parts = routing_key.split(".")
        if len(parts) != 2 or parts[0] != "insight" or parts[1] != envelope["device"]:
            raise ValueError("Insight routing key must be insight.<device>")
        version = payload.get("model_version")
        tags = {"device": envelope["device"]}
        if isinstance(version, str) and version:
            tags["model_version"] = version
        measurement = "insight"
        raw_fields = {key: value for key, value in payload.items() if key != "model_version"}
    else:
        raise ValueError(f"Unsupported writer envelope type: {event_type!r}")

    fields: dict[str, int | float | bool] = {}
    for key, value in raw_fields.items():
        if not FIELD_NAME.fullmatch(key):
            raise ValueError(f"Invalid Influx field name: {key!r}")
        if isinstance(value, bool):
            fields[key] = value
        elif isinstance(value, int):
            fields[key] = value
        elif isinstance(value, float) and math.isfinite(value):
            fields[key] = value
    if not fields:
        raise ValueError("Envelope payload has no finite numeric fields")

    return {"measurement": measurement, "tags": tags, "fields": fields, "time": timestamp}


class Writer:
    """Consumes in bounded batches and acknowledges only after a successful write."""

    def __init__(self, queue: Any, channel: Any, write_api: Any, bucket: str, org: str) -> None:
        self.queue = queue
        self.channel = channel
        self.write_api = write_api
        self.bucket = bucket
        self.org = org
        self.pending: asyncio.Queue[Any] = asyncio.Queue(maxsize=MAX_BATCH_SIZE)

    async def run(self) -> None:
        async def enqueue(message: Any) -> None:
            await self.pending.put(message)

        consumer_tag = await self.queue.consume(enqueue, no_ack=False)
        logger.info("Consuming q.writer with prefetch=%d", MAX_BATCH_SIZE)
        try:
            while True:
                first = await self.pending.get()
                batch = [first]
                deadline = asyncio.get_running_loop().time() + FLUSH_INTERVAL_S
                while len(batch) < MAX_BATCH_SIZE:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(self.pending.get(), timeout=remaining))
                    except asyncio.TimeoutError:
                        break
                await self._flush(batch)
        finally:
            await self.queue.cancel(consumer_tag)

    async def _flush(self, batch: list[Any]) -> None:
        valid: list[tuple[Any, dict[str, Any]]] = []
        for message in batch:
            try:
                routing_key = (message.headers or {}).get(ORIGINAL_ROUTING_KEY_HEADER, message.routing_key)
                if not isinstance(routing_key, str) or not routing_key:
                    raise ValueError("Retry message has no original routing key")
                valid.append((message, to_influx_record(message.body, routing_key)))
            except (ValueError, TypeError) as exc:
                logger.warning("Rejecting invalid q.writer message: %s", exc)
                await message.reject(requeue=False)

        if not valid:
            return
        try:
            result = await self.write_api.write(
                bucket=self.bucket,
                org=self.org,
                record=[record for _, record in valid],
            )
            if result is False:
                raise RuntimeError("InfluxDB write API did not confirm the batch")
        except Exception as exc:
            logger.exception("InfluxDB batch write failed; scheduling retry/DLQ: %s", exc)
            await asyncio.gather(*(self._retry_or_dead_letter(message) for message, _ in valid))
            return

        await asyncio.gather(*(message.ack() for message, _ in valid))

    async def _retry_or_dead_letter(self, message: Any) -> None:
        headers = dict(message.headers or {})
        try:
            retries = int(headers.get(RETRY_HEADER, 0))
        except (TypeError, ValueError):
            retries = 0

        if retries >= MAX_WRITE_RETRIES:
            await message.reject(requeue=False)
            return

        headers[RETRY_HEADER] = retries + 1
        headers.setdefault(ORIGINAL_ROUTING_KEY_HEADER, message.routing_key)
        try:
            import aio_pika

            await asyncio.sleep(RETRY_BACKOFF_S[min(retries, len(RETRY_BACKOFF_S) - 1)])
            retry = aio_pika.Message(
                body=message.body,
                headers=headers,
                content_type=message.content_type,
                content_encoding=message.content_encoding,
                correlation_id=message.correlation_id,
                message_id=message.message_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            exchange = await self.channel.get_exchange(RETRY_EXCHANGE, ensure=False)
            confirmed = await exchange.publish(
                retry,
                routing_key="retry",
                mandatory=True,
            )
            if confirmed is False:
                raise RuntimeError("RabbitMQ did not confirm retry publication")
        except Exception:
            # Do not requeue forever: failed retry publication is dead-lettered.
            logger.exception("Could not publish q.writer retry; dead-lettering original")
            await message.reject(requeue=False)
            return
        await message.ack()


async def _serve() -> None:
    import aio_pika
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

    rabbit_url = os.getenv("RABBITMQ_URL")
    if not rabbit_url:
        username = quote(os.getenv("RABBITMQ_WRITER_USERNAME", "writer"), safe="")
        password = quote(os.environ["RABBITMQ_WRITER_PASSWORD"], safe="")
        host = os.getenv("RABBITMQ_HOST", "localhost")
        port = os.getenv("RABBITMQ_PORT", "5672")
        vhost = quote(os.getenv("RABBITMQ_VHOST", "/"), safe="")
        rabbit_url = f"amqp://{username}:{password}@{host}:{port}/{vhost}"
    influx_url = os.environ["INFLUX_URL"]
    influx_token = _influx_token()
    influx_org = os.getenv("INFLUX_ORG", "unifacef")
    influx_bucket = os.getenv("INFLUX_BUCKET", "tankvitals")

    async with InfluxDBClientAsync(url=influx_url, token=influx_token, org=influx_org) as client:
        connection = await aio_pika.connect_robust(rabbit_url)
        try:
            consumer_channel = await connection.channel()
            await consumer_channel.set_qos(prefetch_count=MAX_BATCH_SIZE)
            queue = await consumer_channel.declare_queue("q.writer", passive=True)
            publisher_channel = await connection.channel(
                publisher_confirms=True,
                on_return_raises=True,
            )
            await Writer(queue, publisher_channel, client.write_api(), influx_bucket, influx_org).run()
        finally:
            await connection.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_serve())


if __name__ == "__main__":
    main()

"""Unit tests for the PLAT Writer envelope-to-Influx mapping."""

import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.writer import RETRY_HEADER, Writer, to_influx_record


class FakeMessage:
    def __init__(self, headers=None):
        self.body = json.dumps({
            "id": "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10",
            "ts": "2026-11-16T13:45:00Z",
            "type": "telemetry",
            "device": "od-01",
            "payload": {"oxygen_mg_l": 6.82},
        }).encode()
        self.routing_key = "telemetry.tanque-01.sensor.od-01"
        self.headers = headers or {}
        self.content_type = "application/json"
        self.content_encoding = None
        self.correlation_id = None
        self.message_id = "message-1"
        self.actions = []

    async def ack(self):
        self.actions.append("ack")

    async def reject(self, requeue=False):
        self.actions.append(("reject", requeue))

    async def nack(self, requeue=False):
        self.actions.append(("nack", requeue))


class FakeWriteApi:
    def __init__(self, result=True):
        self.result = result
        self.records = []

    async def write(self, **kwargs):
        self.records.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeExchange:
    def __init__(self, result=True):
        self.result = result
        self.published = []

    async def publish(self, message, **kwargs):
        self.published.append((message, kwargs))
        return self.result


class FakeChannel:
    def __init__(self, result=True):
        self.default_exchange = FakeExchange(result)


class WriterMappingTests(unittest.TestCase):
    def _body(self, kind="telemetry", payload=None, **updates):
        envelope = {
            "id": "0f6c1d0e-5b7a-4a43-9a52-7c3e2f1b9d10",
            "ts": "2026-11-16T13:45:00Z",
            "type": kind,
            "device": "od-01" if kind == "telemetry" else "bomba-rec-01",
            "payload": payload or {"oxygen_mg_l": 6.82},
        }
        envelope.update(updates)
        return json.dumps(envelope).encode()

    def test_telemetry_record_uses_zone_device_and_kind_tags(self):
        record = to_influx_record(self._body(payload={"oxygen_mg_l": 6.82}), "telemetry.tanque-01.sensor.od-01")
        self.assertEqual(record["measurement"], "telemetry")
        self.assertEqual(record["tags"], {"device": "od-01", "zone": "tanque-01", "kind": "sensor"})
        self.assertEqual(record["fields"], {"oxygen_mg_l": 6.82})
        self.assertEqual(record["time"], datetime(2026, 11, 16, 13, 45, tzinfo=timezone.utc))

    def test_insight_record_uses_insight_measurement_and_model_version_tag(self):
        record = to_influx_record(
            self._body(kind="insight", payload={"health": 72.5, "rul_h": 41.0, "model_version": "v3"}),
            "insight.bomba-rec-01",
        )
        self.assertEqual(record["measurement"], "insight")
        self.assertEqual(record["tags"], {"device": "bomba-rec-01", "model_version": "v3"})
        self.assertEqual(record["fields"], {"health": 72.5, "rul_h": 41.0})

    def test_rejects_malformed_envelope_and_mismatched_route(self):
        with self.assertRaises(ValueError):
            to_influx_record(b"not-json", "telemetry.tanque-01.sensor.od-01")
        with self.assertRaises(ValueError):
            to_influx_record(self._body(), "telemetry.tanque-01.sensor.ph-01")

    def test_rejects_non_finite_or_empty_fields(self):
        with self.assertRaises(ValueError):
            to_influx_record(self._body(payload={"reading": float("nan")}), "telemetry.tanque-01.sensor.od-01")


class WriterDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ack_happens_after_successful_influx_batch(self):
        message = FakeMessage()
        write_api = FakeWriteApi()
        writer = Writer(queue=None, channel=FakeChannel(), write_api=write_api, bucket="tankvitals", org="unifacef")

        await writer._flush([message])

        self.assertEqual(len(write_api.records), 1)
        self.assertEqual(message.actions, ["ack"])

    async def test_write_failure_republishes_twice_then_dead_letters(self):
        channel = FakeChannel()
        writer = Writer(queue=None, channel=channel, write_api=FakeWriteApi(), bucket="tankvitals", org="unifacef")
        fake_aio_pika = SimpleNamespace(
            Message=lambda **kwargs: SimpleNamespace(**kwargs),
            DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
        )
        message = FakeMessage()
        with patch.dict(sys.modules, {"aio_pika": fake_aio_pika}):
            await writer._retry_or_dead_letter(message)
            self.assertEqual(message.actions, ["ack"])
            self.assertEqual(channel.default_exchange.published[0][0].headers[RETRY_HEADER], 1)

            message = FakeMessage(headers={RETRY_HEADER: 1})
            await writer._retry_or_dead_letter(message)
            self.assertEqual(message.actions, ["ack"])
            self.assertEqual(channel.default_exchange.published[1][0].headers[RETRY_HEADER], 2)

            message = FakeMessage(headers={RETRY_HEADER: 2})
            await writer._retry_or_dead_letter(message)
            self.assertEqual(message.actions, [("reject", False)])

    async def test_unconfirmed_retry_keeps_original_delivery(self):
        writer = Writer(
            queue=None,
            channel=FakeChannel(result=False),
            write_api=FakeWriteApi(),
            bucket="tankvitals",
            org="unifacef",
        )
        message = FakeMessage()
        fake_aio_pika = SimpleNamespace(
            Message=lambda **kwargs: SimpleNamespace(**kwargs),
            DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
        )
        with patch.dict(sys.modules, {"aio_pika": fake_aio_pika}):
            await writer._retry_or_dead_letter(message)
        self.assertEqual(message.actions, [("nack", True)])


if __name__ == "__main__":
    unittest.main()

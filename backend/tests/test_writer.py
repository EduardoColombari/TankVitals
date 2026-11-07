"""Unit tests for the PLAT Writer envelope-to-Influx mapping."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.writer import ORIGINAL_ROUTING_KEY_HEADER, RETRY_EXCHANGE, RETRY_HEADER, Writer, _influx_token, to_influx_record


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
        result = self.result.pop(0) if isinstance(self.result, list) else self.result
        if isinstance(result, Exception):
            raise result
        return result


class FakeExchange:
    def __init__(self, result=True):
        self.result = result
        self.published = []

    async def publish(self, message, **kwargs):
        self.published.append((message, kwargs))
        return self.result


class FakeChannel:
    def __init__(self, result=True):
        self.exchange = FakeExchange(result)

    async def get_exchange(self, name, ensure=False):
        if name != RETRY_EXCHANGE or ensure:
            raise AssertionError("Writer must use the predeclared dedicated retry exchange")
        return self.exchange


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


class WriterTokenTests(unittest.TestCase):
    def test_reads_scoped_token_from_secret_file_before_environment_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "writer.token"
            token_path.write_text("writer-scoped-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"INFLUX_TOKEN_FILE": str(token_path), "INFLUX_TOKEN": "admin-token"}):
                self.assertEqual(_influx_token(), "writer-scoped-token")

    def test_refuses_empty_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "writer.token"
            token_path.write_text("  \n", encoding="utf-8")
            with patch.dict(os.environ, {"INFLUX_TOKEN_FILE": str(token_path)}, clear=False):
                with patch.dict(os.environ, {"INFLUX_TOKEN": ""}):
                    with self.assertRaisesRegex(RuntimeError, "Writer-scoped token file"):
                        _influx_token()


class WriterDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ack_happens_after_successful_influx_batch(self):
        message = FakeMessage()
        write_api = FakeWriteApi()
        writer = Writer(queue=None, channel=FakeChannel(), write_api=write_api, bucket="tankvitals", org="unifacef")

        await writer._flush([message])

        self.assertEqual(len(write_api.records), 1)
        self.assertEqual(message.actions, ["ack"])

    async def test_transient_influx_failure_retries_once_then_acknowledges(self):
        channel = FakeChannel()
        write_api = FakeWriteApi([RuntimeError("temporary"), True])
        writer = Writer(
            queue=None,
            channel=channel,
            write_api=write_api,
            bucket="tankvitals",
            org="unifacef",
        )
        fake_aio_pika = SimpleNamespace(
            Message=lambda **kwargs: SimpleNamespace(**kwargs),
            DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
        )
        message = FakeMessage()
        with patch.dict(sys.modules, {"aio_pika": fake_aio_pika}):
            with patch("app.writer.asyncio.sleep", new_callable=AsyncMock) as sleep:
                await writer._flush([message])
                sleep.assert_awaited_once_with(1.0)
            self.assertEqual(message.actions, ["ack"])
            retry = channel.exchange.published[0][0]
            self.assertEqual(retry.headers[RETRY_HEADER], 1)
            self.assertEqual(retry.headers[ORIGINAL_ROUTING_KEY_HEADER], message.routing_key)
            self.assertEqual(channel.exchange.published[0][1]["routing_key"], "retry")

            retried_delivery = FakeMessage(headers=retry.headers)
            retried_delivery.body = retry.body
            retried_delivery.routing_key = "retry"
            await writer._flush([retried_delivery])
        self.assertEqual(retried_delivery.actions, ["ack"])
        self.assertEqual(len(write_api.records), 2)

    async def test_permanent_influx_failure_has_three_attempts_then_dead_letters(self):
        channel = FakeChannel()
        write_api = FakeWriteApi(RuntimeError("permanent"))
        writer = Writer(queue=None, channel=channel, write_api=write_api, bucket="tankvitals", org="unifacef")
        fake_aio_pika = SimpleNamespace(
            Message=lambda **kwargs: SimpleNamespace(**kwargs),
            DeliveryMode=SimpleNamespace(PERSISTENT="persistent"),
        )
        message = FakeMessage()
        with patch.dict(sys.modules, {"aio_pika": fake_aio_pika}):
            with patch("app.writer.asyncio.sleep", new_callable=AsyncMock) as sleep:
                for attempt in range(3):
                    await writer._flush([message])
                    if attempt < 2:
                        retry = channel.exchange.published[attempt][0]
                        message = FakeMessage(headers=retry.headers)
                        message.body = retry.body
                        message.routing_key = "retry"
                self.assertEqual([call.args[0] for call in sleep.await_args_list], [1.0, 2.0])
        self.assertEqual(len(write_api.records), 3)
        self.assertEqual(len(channel.exchange.published), 2)
        self.assertEqual(message.actions, [("reject", False)])

    async def test_unconfirmed_retry_dead_letters_original_without_requeue_loop(self):
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
            with patch("app.writer.asyncio.sleep", new_callable=AsyncMock):
                await writer._retry_or_dead_letter(message)
        self.assertEqual(message.actions, [("reject", False)])

    async def test_retry_delivery_uses_original_route_for_record_mapping(self):
        message = FakeMessage(headers={ORIGINAL_ROUTING_KEY_HEADER: "telemetry.tanque-01.sensor.od-01"})
        message.routing_key = "retry"
        writer = Writer(queue=None, channel=FakeChannel(), write_api=FakeWriteApi(), bucket="tankvitals", org="unifacef")
        await writer._flush([message])
        self.assertEqual(message.actions, ["ack"])


if __name__ == "__main__":
    unittest.main()

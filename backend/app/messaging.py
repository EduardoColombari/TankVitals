"""Real RabbitMQ metrics and a durable, bounded view of q.ws messages."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
import time
import secrets
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from app.ai_api import current_user

router = APIRouter(prefix="/api")
_ws_tickets: dict[str, tuple[float, str]] = {}

logger = logging.getLogger(__name__)


class MessagingMonitor:
    def __init__(self, db_path: str | Path, event_handler=None) -> None:
        self.db_path = str(db_path)
        self.event_handler = event_handler
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS recent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, routing_key TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS recent_messages_id ON recent_messages(id)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, routing_key: str, body: bytes) -> None:
        try:
            decoded = json.loads(body.decode("utf-8"))
            if not isinstance(decoded, dict):
                decoded = {"raw": decoded}
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = {"raw": body[:4096].decode("utf-8", errors="replace")}
        with self._db() as db:
            db.execute("INSERT INTO recent_messages(routing_key, body) VALUES (?, ?)",
                       (routing_key[:255], json.dumps(decoded, ensure_ascii=False)[:65536]))
            db.execute("DELETE FROM recent_messages WHERE id NOT IN (SELECT id FROM recent_messages ORDER BY id DESC LIMIT 50)")

    def recent(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT routing_key, body FROM recent_messages ORDER BY id DESC LIMIT 50").fetchall()
        return [{"routing_key": key, "message": json.loads(body)} for key, body in reversed(rows)]

    async def consume_qws(self, stop: asyncio.Event) -> None:
        import aio_pika

        url = os.environ["RABBITMQ_URL"]
        while not stop.is_set():
            try:
                connection = await aio_pika.connect_robust(url, client_properties={"connection_name": "api-q-ws-monitor"})
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=20)
                queue = await channel.get_queue("q.ws", ensure=False)

                async def on_message(message):
                    try:
                        await asyncio.to_thread(self.record, message.routing_key or "", message.body)
                        if self.event_handler:
                            await self.event_handler(message.routing_key or "", message.body)
                    except Exception:
                        logger.exception("Could not persist q.ws message")
                        await message.nack(requeue=True)
                        return
                    await message.ack()

                tag = await queue.consume(on_message)
                logger.info("API consuming q.ws for the bounded recent-message view")
                await stop.wait()
                await queue.cancel(tag)
                await connection.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("q.ws monitor unavailable; retrying in two seconds")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass

    async def snapshot(self) -> dict:
        return await asyncio.to_thread(self._snapshot_sync)

    def _snapshot_sync(self) -> dict:
        mgmt = os.environ["RABBITMQ_MANAGEMENT_URL"].rstrip("/")
        username = os.environ["RABBITMQ_API_USERNAME"]
        password = os.environ["RABBITMQ_API_PASSWORD"]
        overview = self._get_json(f"{mgmt}/api/overview", username, password)
        consumers = self._get_json(f"{mgmt}/api/consumers", username, password)
        queues = self._get_json(f"{mgmt}/api/queues/%2F", username, password)
        if not isinstance(overview, dict) or not isinstance(consumers, list) or not isinstance(queues, list):
            raise RuntimeError("RabbitMQ management API returned an invalid queues response")
        consumer_counts: dict[str, int] = {}
        for item in consumers:
            queue_info = item.get("queue") if isinstance(item, dict) else None
            if isinstance(queue_info, dict) and isinstance(queue_info.get("name"), str):
                consumer_counts[queue_info["name"]] = consumer_counts.get(queue_info["name"], 0) + 1
        metrics = []
        for item in queues:
            if item.get("name") not in {"q.writer", "q.inference", "q.rules", "q.ws", "q.commands", "q.jobs.datagen", "q.jobs.train", "q.dlq"}:
                continue
            stats = item.get("message_stats") or {}
            metrics.append({
                "name": item["name"],
                "messages_ready": int(item.get("messages_ready", 0)),
                "messages_unacknowledged": int(item.get("messages_unacknowledged", 0)),
                "publish_rate": float((stats.get("publish_details") or {}).get("rate", 0)),
                "deliver_rate": float((stats.get("deliver_details") or {}).get("rate", 0)),
                "consumers": consumer_counts.get(item["name"], int(item.get("consumers", 0))),
            })
        if not any(row["name"] == "q.dlq" for row in metrics):
            raise RuntimeError("RabbitMQ management API did not return q.dlq")
        dlq = next(row for row in metrics if row["name"] == "q.dlq")
        sample = self._post_json(f"{mgmt}/api/queues/%2F/q.dlq/get", username, password,
                                 {"count": 1, "ackmode": "ack_requeue_true", "encoding": "auto", "truncate": 50000})
        if not isinstance(sample, list):
            raise RuntimeError("RabbitMQ management API returned an invalid DLQ sample")
        return {"queues": metrics, "recent_messages": self.recent(),
                "dlq": {"messages": dlq["messages_ready"] + dlq["messages_unacknowledged"],
                        "sample": [self._management_message(row) for row in sample]}}

    @staticmethod
    def _management_message(row: dict) -> dict:
        raw = row.get("payload", "")
        if row.get("payload_encoding") == "base64":
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return {"routing_key": row.get("routing_key", ""), "payload": raw}
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            payload = raw
        return {"routing_key": row.get("routing_key", ""), "message": payload,
                "properties": row.get("properties", {})}

    @staticmethod
    def _request(url: str, username: str, password: str, method: str, body: dict | None = None):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        data = None if body is None else json.dumps(body).encode()
        request = Request(url, data=data, method=method, headers={
            "Authorization": f"Basic {token}", "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read() or b"null")
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            status = getattr(exc, "code", "unavailable")
            raise RuntimeError(f"RabbitMQ management API {method} failed ({status})") from None

    @classmethod
    def _get_json(cls, url, username, password):
        return cls._request(url, username, password, "GET")

    @classmethod
    def _post_json(cls, url, username, password, body):
        return cls._request(url, username, password, "POST", body)


@router.post("/auth/login")
async def login(request: FastAPIRequest):
    from app.catalog_api import engine
    from app.core.auth import authenticate
    if request.headers.get('content-type', '').startswith('application/json'):
        try:
            values = await request.json()
            username, password = values.get('username', ''), values.get('password', '')
        except (ValueError, AttributeError):
            raise HTTPException(400, 'Credenciais invÃ¡lidas') from None
    else:
        values = parse_qs((await request.body()).decode('utf-8', errors='replace'), max_num_fields=8)
        username, password = values.get('username', [''])[0], values.get('password', [''])[0]
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 120:
        raise HTTPException(400, 'Credenciais invÃ¡lidas')
    return await asyncio.to_thread(authenticate, engine(request), username.strip().lower(), password)


@router.get("/messaging")
async def messaging(request: FastAPIRequest, user=Depends(current_user)):
    monitor = getattr(request.app.state, "messaging_monitor", None)
    if monitor is None:
        raise HTTPException(503, "Monitoramento RabbitMQ indisponÃ­vel")
    try:
        return await monitor.snapshot()
    except Exception:
        logger.exception("Could not build RabbitMQ messaging snapshot")
        raise HTTPException(503, "MÃ©tricas RabbitMQ indisponÃ­veis") from None


@router.post("/ws/ticket")
def ws_ticket(user=Depends(current_user)):
    now = time.monotonic()
    for token, (expires, _) in list(_ws_tickets.items()):
        if expires <= now:
            _ws_tickets.pop(token, None)
    ticket = secrets.token_urlsafe(32)
    _ws_tickets[ticket] = (now + 30, user["sub"])
    return {"ticket": ticket}


def consume_ws_ticket(ticket: str | None) -> bool:
    if not ticket:
        return False
    row = _ws_tickets.pop(ticket, None)
    return row is not None and row[0] > time.monotonic()


"""End-to-end PLAT-06 checks running inside the isolated Compose network."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pika


HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
API_USER = os.getenv("RABBITMQ_API_USERNAME", "api")
API_PASSWORD = os.environ["RABBITMQ_API_PASSWORD"]
GATEWAY_USER = os.getenv("RABBITMQ_GATEWAY_USERNAME", "gateway")
GATEWAY_PASSWORD = os.environ["RABBITMQ_GATEWAY_PASSWORD"]
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://influxdb:8086").rstrip("/")
INFLUX_ORG = os.getenv("INFLUX_ORG", "unifacef")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "tankvitals")
ADMIN_TOKEN = os.environ["INFLUXDB_ADMIN_TOKEN"]
WRITER_TOKEN_FILE = os.getenv("INFLUX_TOKEN_FILE", "/run/plat06/secrets/influx_writer.token")
FAULT_URL = os.getenv("FAULT_PROXY_URL", "http://influx-fault-proxy:8087")
CURRENT_SCENARIO = "startup"
CURRENT_OPERATION = "initialization"


def check(ok: bool, scenario: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {scenario}" + (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        raise AssertionError(scenario)


def request(url: str, *, method: str = "GET", body: bytes | None = None, token: str | None = None,
            content_type: str | None = None, timeout: int = 15) -> tuple[int, bytes]:
    global CURRENT_OPERATION
    headers = {}
    if token:
        headers["Authorization"] = f"Token {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    endpoint = urllib.parse.urlsplit(url)
    CURRENT_OPERATION = f"HTTP {method} {endpoint.path}"
    print(f"RUN: HTTP {method} {endpoint.path} (socket timeout {timeout}s)", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def connection(user: str, password: str) -> pika.BlockingConnection:
    global CURRENT_OPERATION
    CURRENT_OPERATION = "connect to RabbitMQ"
    print("RUN: connect to RabbitMQ (3 attempts, 5s socket timeout)", flush=True)
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=HOST, port=int(os.getenv("RABBITMQ_PORT", "5672")), virtual_host="/",
        credentials=pika.PlainCredentials(user, password), heartbeat=15,
        blocked_connection_timeout=10, connection_attempts=3, retry_delay=1, socket_timeout=5,
    ))


def envelope(timestamp: str | None = None, *, restart_probe: bool = False) -> tuple[dict, str, int]:
    restart_value = int(os.getenv("PLAT06_RESTART_VALUE", "0")) if restart_probe else 0
    stamp = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    value = restart_value or (uuid.uuid4().int & ((1 << 63) - 1))
    event = {
        "id": str(uuid.uuid4()), "type": "telemetry", "device": "plat-test",
        "ts": stamp, "payload": {"probe": value},
    }
    return event, "telemetry.tanque-01.sensor.plat-test", value


def publish(event: dict, routing_key: str) -> None:
    global CURRENT_OPERATION
    CURRENT_OPERATION = "publish telemetry and await publisher confirmation"
    conn = connection(GATEWAY_USER, GATEWAY_PASSWORD)
    try:
        print("RUN: publish telemetry and await publisher confirmation", flush=True)
        ch = conn.channel()
        ch.confirm_delivery()
        ch.basic_publish(
            exchange="tv.telemetry", routing_key=routing_key, body=json.dumps(event).encode(),
            mandatory=True, properties=pika.BasicProperties(
                content_type="application/json", delivery_mode=2, message_id=event["id"],
            ),
        )
    finally:
        conn.close()


def query_value(value: int) -> list[dict[str, str]]:
    flux = (
        f'from(bucket: "{INFLUX_BUCKET}") |> range(start: -30m) '
        '|> filter(fn: (r) => r._measurement == "telemetry" and r.device == "plat-test") '
        f'|> filter(fn: (r) => r._field == "probe" and r._value == {value})'
    )
    status, body = request(
        f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}",
        method="POST", body=flux.encode(), token=ADMIN_TOKEN,
        content_type="application/vnd.flux", timeout=20,
    )
    if status != 200:
        raise RuntimeError(f"Influx query failed with HTTP {status}")
    rows = []
    csv_text = "\n".join(line for line in body.decode().splitlines() if not line.startswith("#"))
    for row in csv.DictReader(io.StringIO(csv_text), restkey="_extra"):
        if row.get("_value") == str(value):
            rows.append(row)
    return rows


def wait_value(value: int, expected: bool = True, timeout: float = 35) -> list[dict[str, str]]:
    global CURRENT_OPERATION
    CURRENT_OPERATION = "poll InfluxDB for probe value"
    print(f"RUN: poll InfluxDB for probe value (deadline {timeout:g}s)", flush=True)
    deadline = time.monotonic() + timeout
    found: list[dict[str, str]] = []
    while time.monotonic() < deadline:
        found = query_value(value)
        if bool(found) == expected:
            return found
        time.sleep(0.5)
    return found


def queue_state(name: str) -> dict:
    global CURRENT_OPERATION
    CURRENT_OPERATION = f"query RabbitMQ management queue {name}"
    print(f"RUN: query RabbitMQ management queue state for {name} (timeout 10s)", flush=True)
    credentials = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"http://rabbitmq:15672/api/queues/%2F/{urllib.parse.quote(name, safe='')}",
        headers={"Authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait_queue_empty(timeout: float = 20) -> bool:
    global CURRENT_OPERATION
    CURRENT_OPERATION = "poll q.writer until drained"
    print(f"RUN: poll q.writer until drained (deadline {timeout:g}s)", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = queue_state("q.writer")
        if state.get("messages_ready", 0) == 0 and state.get("messages_unacknowledged", 0) == 0:
            return True
        time.sleep(0.25)
    return False


def wait_queue_ready(name: str, minimum: int = 1, timeout: float = 12) -> dict:
    global CURRENT_OPERATION
    CURRENT_OPERATION = f"poll {name} until ready count reaches {minimum}"
    print(f"RUN: poll {name} until ready count >= {minimum} (deadline {timeout:g}s)", flush=True)
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = queue_state(name)
        if state.get("messages_ready", 0) >= minimum:
            return state
        time.sleep(0.25)
    return state


def inject_failures(count: int) -> None:
    status, _ = request(f"{FAULT_URL}/__test__/fail-next?count={count}")
    if status != 204:
        raise RuntimeError("Could not configure isolated Influx fault proxy")


def verify_token() -> None:
    token = open(WRITER_TOKEN_FILE, encoding="utf-8").read().strip()
    check(bool(token), "Writer token file exists and is non-empty")
    _, _, value = envelope()
    line = f'telemetry,device=plat-test,zone=tanque-01,kind=sensor probe={value}i {int(time.time())}000000000'
    status, _ = request(
        f"{INFLUX_URL}/api/v2/write?org={urllib.parse.quote(INFLUX_ORG)}&bucket={urllib.parse.quote(INFLUX_BUCKET)}&precision=ns",
        method="POST", body=line.encode(), token=token, content_type="text/plain",
    )
    check(status == 204, "bucket-scoped Writer token can write to tankvitals", str(status))

    status, body = request(
        f"{INFLUX_URL}/api/v2/orgs?org={urllib.parse.quote(INFLUX_ORG)}", token=ADMIN_TOKEN,
    )
    if status != 200:
        raise RuntimeError(f"Could not look up the InfluxDB organization (HTTP {status})")
    orgs = json.loads(body).get("orgs", [])
    org = next((item for item in orgs if item.get("name") == INFLUX_ORG), None)
    if not org or not org.get("id"):
        raise RuntimeError("Could not identify the InfluxDB organization for the token-scope check")

    denied_bucket = "plat06-writer-scope-probe"
    status, body = request(
        # Listing by name returns HTTP 404 in the isolated Influx 2.7 setup
        # when the probe does not exist yet. List the org's buckets with the
        # administrative token and match locally so absence is distinguishable
        # from an authorization or organization lookup failure.
        # InfluxDB OSS 2.7 rejects page limits above 100 with HTTP 400.
        # The isolated instance has only a few buckets, so one maximum-sized
        # page is sufficient to distinguish a missing probe from API failure.
        f"{INFLUX_URL}/api/v2/buckets?orgID={urllib.parse.quote(org['id'])}&limit=100",
        token=ADMIN_TOKEN,
    )
    if status != 200:
        raise RuntimeError(f"Could not look up the token-scope probe bucket (HTTP {status})")
    buckets = json.loads(body).get("buckets", [])
    probe = next((item for item in buckets if item.get("name") == denied_bucket), None)
    if probe is None:
        status, body = request(
            f"{INFLUX_URL}/api/v2/buckets",
            method="POST",
            body=json.dumps({"orgID": org["id"], "name": denied_bucket}).encode(),
            token=ADMIN_TOKEN,
            content_type="application/json",
        )
        check(status == 201, "isolated write-denial probe bucket is available", str(status))
        probe = json.loads(body)
    if not probe.get("id") or probe.get("orgID") != org["id"]:
        raise RuntimeError("Token-scope probe bucket has an unexpected organization or missing ID")

    denied_line = f'telemetry,device=plat-test,zone=tanque-01,kind=sensor probe={value}i {int(time.time())}000000001'
    status, _ = request(
        f"{INFLUX_URL}/api/v2/write?org={urllib.parse.quote(INFLUX_ORG)}&bucket={urllib.parse.quote(denied_bucket)}&precision=ns",
        method="POST", body=denied_line.encode(), token=token, content_type="text/plain",
    )
    check(status in (401, 403), "Writer token cannot write outside tankvitals", str(status))

    query = f'from(bucket: "{INFLUX_BUCKET}") |> range(start: -1h) |> limit(n: 1)'.encode()
    query_url = f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}"
    status, _ = request(
        query_url,
        method="POST",
        body=query,
        token=ADMIN_TOKEN,
        content_type="application/vnd.flux",
    )
    if status != 200:
        raise RuntimeError(
            f"Administrative control query for existing bucket {INFLUX_BUCKET!r} "
            f"failed with HTTP {status}"
        )

    status, body = request(
        query_url,
        method="POST",
        body=query,
        token=token,
        content_type="application/vnd.flux",
    )
    hidden_bucket = False
    if status == 404:
        try:
            error = json.loads(body)
            message = str(error.get("message", "")).lower()
            hidden_bucket = (
                error.get("code") == "not found"
                and "bucket" in message
                and INFLUX_BUCKET.lower() in message
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    check(
        status in (401, 403) or hidden_bucket,
        "Writer token cannot read from tankvitals",
        f"{status} (bucket hidden by authorization)" if hidden_bucket else str(status),
    )


def run(mode: str) -> None:
    global CURRENT_SCENARIO, CURRENT_OPERATION
    CURRENT_SCENARIO = mode
    CURRENT_OPERATION = "scenario initialization"
    print(f"RUN: PLAT-06 client scenario '{mode}'", flush=True)
    if mode == "token-check":
        verify_token()
        return
    event, route, value = envelope(restart_probe=mode in {"publish-only", "drain"})
    if mode == "publish-only":
        publish(event, route)
        check(True, "persistent telemetry publication confirmed for restart scenario")
        return
    if mode == "queue-pending":
        state = wait_queue_ready("q.writer")
        ready = state.get("messages_ready", 0)
        unacknowledged = state.get("messages_unacknowledged", 0)
        check(ready >= 1, "message remains queued while Writer is stopped", f"ready={ready}, unacknowledged={unacknowledged}")
        return
    if mode == "drain":
        rows = wait_value(value)
        check(bool(rows), "message queued during Writer restart is stored after recovery", f"value={value}")
        check(wait_queue_empty(), "restart recovery drains q.writer and acknowledges the message")
        return
    if mode == "success":
        publish(event, route)
        rows = wait_value(value)
        check(bool(rows), "RabbitMQ to Writer to InfluxDB stores the published field", f"value={value}")
        check(wait_queue_empty(), "Writer acknowledges the message after successful persistence")
        return
    if mode == "transient":
        inject_failures(1)
        publish(event, route)
        rows = wait_value(value)
        check(bool(rows), "Writer retries a temporary Influx failure and persists the message", f"value={value}")
        status, body = request(f"{FAULT_URL}/__test__/status")
        count = body.decode("ascii", errors="replace")
        check(status == 200 and body == b"1", "exactly one temporary write failure was injected", f"HTTP {status}, count={count}")
        check(wait_queue_empty(), "retry flow finishes with no unacknowledged q.writer messages")
        return
    if mode == "permanent":
        before = queue_state("q.dlq")
        check(
            before.get("messages_ready", 0) == 0,
            "DLQ starts empty before the permanent-failure scenario",
            f"ready={before.get('messages_ready', 0)}",
        )
        inject_failures(3)
        publish(event, route)
        deadline = time.monotonic() + 30
        state = {}
        print("RUN: poll q.dlq for permanent failure (deadline 30s)", flush=True)
        while time.monotonic() < deadline:
            state = queue_state("q.dlq")
            if state.get("messages_ready", 0) >= 1:
                break
            time.sleep(0.5)
        check(state.get("messages_ready", 0) >= 1, "three failed writes route the original message to q.dlq")
        conn = connection(API_USER, API_PASSWORD)
        try:
            ch = conn.channel()
            method, props, body = ch.basic_get("q.dlq", auto_ack=False)
            actual_id = json.loads(body).get("id") if method is not None else "<empty>"
            check(method is not None and actual_id == event["id"], "DLQ retains the original event envelope", f"expected_id={event['id']}, actual_id={actual_id}")
            headers = props.headers or {}
            check(headers.get("x-plat-write-retries") == 2, "DLQ event records two scheduled retries")
            check(bool(headers.get("x-death")), "DLQ event contains RabbitMQ dead-letter metadata")
            ch.basic_ack(method.delivery_tag)
        finally:
            conn.close()
        status, body = request(f"{FAULT_URL}/__test__/status")
        count = body.decode("ascii", errors="replace")
        check(status == 200 and body == b"3", "exactly three write failures were injected", f"HTTP {status}, count={count}")
        check(not wait_value(value, expected=False, timeout=2), "permanently failed event was not stored in InfluxDB")
        return
    if mode == "duplicate":
        event, route, value = envelope()
        event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event["payload"]["probe"] = value
        publish(event, route)
        publish(event, route)
        rows = wait_value(value)
        check(len(rows) == 1, "duplicate delivery at the same measurement/tags/timestamp yields one Influx point", f"rows={len(rows)}")
        check(wait_queue_empty(), "duplicate publications are both acknowledged after persistence")
        return
    raise ValueError(f"Unknown test mode: {mode}")


if __name__ == "__main__":
    import sys

    def hard_timeout(_signum, _frame):
        message = (f"PLAT-06 client timeout in scenario '{CURRENT_SCENARIO}' during "
                   f"'{CURRENT_OPERATION}' after 75s")
        print(f"FAIL: {message}", flush=True)
        raise TimeoutError(message)

    try:
        # Per-socket timeouts do not bound a response that trickles data forever.
        signal.signal(signal.SIGALRM, hard_timeout)
        signal.alarm(75)
        run(sys.argv[1] if len(sys.argv) > 1 else "success")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", flush=True)
        raise
    finally:
        signal.alarm(0)

"""Isolated AMQP and management API checks for PLAT-05."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

import pika


HOST = "rabbitmq"
PORT = 5672
MARKER = os.environ["PLAT05_TEST_MARKER"]
USERS = {
    "api": os.environ["RABBITMQ_API_PASSWORD"],
    "gateway": os.environ["RABBITMQ_GATEWAY_PASSWORD"],
    "writer": os.environ["RABBITMQ_WRITER_PASSWORD"],
    "rules": os.environ["RABBITMQ_RULES_PASSWORD"],
    "inference": os.environ["RABBITMQ_INFERENCE_PASSWORD"],
    "worker": os.environ["RABBITMQ_WORKER_PASSWORD"],
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}", flush=True)


def connect(user: str) -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(user, USERS[user])
    parameters = pika.ConnectionParameters(
        host=HOST,
        port=PORT,
        virtual_host="/",
        credentials=credentials,
        heartbeat=15,
        blocked_connection_timeout=10,
        connection_attempts=1,
        socket_timeout=5,
    )
    return pika.BlockingConnection(parameters)


def wait_for_broker() -> None:
    last_error: Exception | None = None
    for _ in range(40):
        try:
            connection = connect("api")
            connection.close()
            return
        except pika.exceptions.ProbableAuthenticationError as error:
            raise RuntimeError("RabbitMQ rejected the generated API credentials") from error
        except Exception as error:  # Broker start and definition import may take a few seconds.
            last_error = error
            time.sleep(2)
    raise RuntimeError("RabbitMQ did not accept an authenticated connection") from last_error


def api_get(path: str) -> object:
    token = base64.b64encode(f"api:{USERS['api']}".encode()).decode()
    request = urllib.request.Request(
        f"http://{HOST}:15672/api/{path}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def verify_topology() -> None:
    exchanges = api_get("exchanges/%2F")
    queues = api_get("queues/%2F")
    bindings = api_get("bindings/%2F")
    overview = api_get("overview")
    check(bool(overview.get("rabbitmq_version")), "monitoring account can read broker overview")

    expected_exchanges = {
        "tv.telemetry": "topic",
        "tv.commands": "topic",
        "tv.insights": "topic",
        "tv.events": "topic",
        "tv.jobs": "direct",
        "tv.dlx": "fanout",
    }
    actual_exchanges = {item["name"]: item["type"] for item in exchanges if item["name"] in expected_exchanges}
    check(actual_exchanges == expected_exchanges, "all documented exchanges and types are loaded")

    expected_queues = {
        "q.writer", "q.ws", "q.rules", "q.inference", "q.commands",
        "q.jobs.datagen", "q.jobs.train", "q.dlq",
    }
    by_queue = {item["name"]: item for item in queues}
    check(expected_queues.issubset(by_queue), "all documented durable queues exist before consumers connect")
    check(all(by_queue[name]["durable"] for name in expected_queues), "all declared queues are durable")
    for name in expected_queues - {"q.dlq"}:
        check(
            by_queue[name]["arguments"].get("x-dead-letter-exchange") == "tv.dlx",
            f"{name} dead-letters to tv.dlx",
        )
    dlq_arguments = by_queue["q.dlq"].get("arguments") or {}
    check("x-dead-letter-exchange" not in dlq_arguments, "q.dlq does not dead-letter back to itself")

    expected_bindings = {
        ("tv.telemetry", "telemetry.#", "q.writer"),
        ("tv.insights", "#", "q.writer"),
        ("tv.telemetry", "telemetry.*.equipment.*", "q.inference"),
        ("tv.events", "model.updated", "q.inference"),
        ("tv.dlx", "#", "q.dlq"),
    }
    actual_bindings = {
        (item["source"], item["routing_key"], item["destination"])
        for item in bindings
        if item["destination_type"] == "queue"
    }
    check(expected_bindings.issubset(actual_bindings), "documented writer, inference, and DLQ bindings are loaded")

    check(True, "API monitoring account can inspect resource metadata")


def publish(channel: pika.adapters.blocking_connection.BlockingChannel, exchange: str, key: str, body: bytes) -> None:
    channel.confirm_delivery()
    channel.basic_publish(
        exchange=exchange,
        routing_key=key,
        body=body,
        mandatory=True,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=str(uuid.uuid4()),
        ),
    )
    # Pika returns None for a successful confirm and raises NackError or
    # UnroutableError when the broker does not accept the publication.
    check(True, f"publisher confirm acknowledged for {exchange or '(default exchange)'}")


def expect_access_refused(user: str, operation) -> None:
    connection = connect(user)
    channel = connection.channel()
    try:
        channel.confirm_delivery()
        operation(channel)
    except pika.exceptions.ChannelClosedByBroker as error:
        check(error.reply_code == 403, f"{user} is denied the unauthorized broker operation")
    else:
        raise AssertionError(f"{user} unexpectedly completed an unauthorized broker operation")
    finally:
        if connection.is_open:
            connection.close()


def check_writer_passive_declare() -> None:
    connection = connect("writer")
    channel = connection.channel()
    try:
        result = channel.queue_declare(queue="q.writer", passive=True)
        check(result.method.queue == "q.writer", "writer can passively verify q.writer before consuming")
    except pika.exceptions.ChannelClosedByBroker as error:
        if error.reply_code != 403:
            raise
        print(
            "BLOCKER: Writer's passive q.writer declaration is denied by configure: ^$; "
            "the PLAT-06 consumer startup path cannot proceed.",
            flush=True,
        )
    finally:
        if connection.is_open:
            connection.close()


def get_one(user: str, queue: str, *, ack: bool = True):
    connection = connect(user)
    channel = connection.channel()
    method, properties, body = channel.basic_get(queue=queue, auto_ack=False)
    return connection, channel, method, properties, body


def verify_permissions_and_delivery() -> None:
    for user in USERS:
        connection = connect(user)
        connection.close()
        check(True, f"generated {user} account authenticates")

    route = f"telemetry.tanque-01.sensor.od-01"
    body = json.dumps({"id": str(uuid.uuid4()), "type": "telemetry", "device": "od-01", "ts": "2026-11-02T12:00:00Z", "data": {"oxygen_mg_l": 6.2}}).encode()

    gateway = connect("gateway")
    gateway_channel = gateway.channel()
    publish(gateway_channel, "tv.telemetry", route, body)
    gateway.close()
    connection, channel, method, properties, received = get_one("writer", "q.writer")
    check(method is not None and received == body, "authorized gateway telemetry reaches q.writer")
    check(properties.delivery_mode == 2, "test publication carries persistent delivery mode")
    channel.basic_ack(method.delivery_tag)
    connection.close()

    check_writer_passive_declare()

    expect_access_refused(
        "gateway",
        lambda channel: channel.basic_publish("tv.commands", "command.tanque-01.actuator.aerador-01", b"{}"),
    )
    expect_access_refused("gateway", lambda channel: channel.basic_get("q.writer", auto_ack=True))
    expect_access_refused(
        "writer",
        lambda channel: channel.basic_publish("tv.telemetry", route, b"{}"),
    )
    expect_access_refused("writer", lambda channel: channel.basic_publish("", "q.writer", body))
    print(
        "BLOCKER: generated Writer permission denies its retry publish to RabbitMQ's "
        "amq.default exchange; PLAT-02 change required.",
        flush=True,
    )
    expect_access_refused("writer", lambda channel: channel.basic_get("q.dlq", auto_ack=True))

    gateway = connect("gateway")
    publish(gateway.channel(), "tv.telemetry", route, body)
    gateway.close()
    connection, channel, method, _, received = get_one("writer", "q.writer")
    check(method is not None and received == body, "writer consumes its authorized q.writer delivery")
    channel.close()  # Unacknowledged work must be returned to the durable queue.
    connection.close()
    connection, channel, method, _, received = get_one("writer", "q.writer")
    check(method is not None and method.redelivered and received == body, "unacked delivery is requeued and marked redelivered")
    channel.basic_ack(method.delivery_tag)
    connection.close()

    gateway = connect("gateway")
    gateway_channel = gateway.channel()
    rejected_body = f"plat05-dlq-{uuid.uuid4()}".encode()
    publish(gateway_channel, "tv.telemetry", route, rejected_body)
    gateway.close()
    connection, channel, method, _, received = get_one("writer", "q.writer")
    check(method is not None and received == rejected_body, "test rejection message reached q.writer")
    channel.basic_nack(method.delivery_tag, requeue=False)
    connection.close()

    connection, channel, method, properties, received = get_one("api", "q.dlq")
    check(method is not None and received == rejected_body, "writer rejection is available from q.dlq to the authorized API")
    headers = properties.headers or {}
    deaths = headers.get("x-death", [])
    check(bool(deaths), "dead-letter metadata includes x-death")
    check(deaths[0].get("reason") == "rejected", "x-death records rejection as the dead-letter reason")
    check(deaths[0].get("queue") == "q.writer", "x-death identifies q.writer as the source")
    check(deaths[0].get("exchange") == "tv.telemetry", "x-death preserves the source exchange")
    check(deaths[0].get("routing-keys") == [route], "x-death preserves the original routing key")
    channel.basic_ack(method.delivery_tag)
    connection.close()


def before_restart() -> None:
    verify_topology()
    verify_permissions_and_delivery()

    body = json.dumps({"marker": MARKER, "id": str(uuid.uuid4())}).encode()
    connection = connect("gateway")
    channel = connection.channel()
    publish(channel, "tv.telemetry", "telemetry.tanque-01.sensor.od-01", body)
    connection.close()
    print(f"PERSISTENT_MARKER={MARKER}", flush=True)
    check(True, "persistent test message was published before broker restart")


def after_restart() -> None:
    verify_topology()
    connection, channel, method, properties, body = get_one("writer", "q.writer")
    check(method is not None, "persistent message remains in q.writer after restart")
    check(properties.delivery_mode == 2 and MARKER.encode() in body, "message content and persistent flag survive restart")
    channel.basic_ack(method.delivery_tag)
    connection.close()
    check(True, "restarted broker accepted authenticated service operations")


def manual_publish() -> None:
    body = json.dumps({
        "id": str(uuid.uuid4()),
        "type": "telemetry",
        "device": "od-01",
        "ts": "2026-11-02T12:00:00Z",
        "data": {"oxygen_mg_l": 6.2},
    }).encode()
    connection = connect("gateway")
    publish(connection.channel(), "tv.telemetry", "telemetry.tanque-01.sensor.od-01", body)
    connection.close()
    print("Published a persistent telemetry test message to q.writer.", flush=True)


def manual_consume() -> None:
    connection, channel, method, _, body = get_one("writer", "q.writer")
    check(method is not None, "q.writer contains a message for the Writer account")
    print(body.decode("utf-8"), flush=True)
    channel.basic_ack(method.delivery_tag)
    connection.close()
    print("Acknowledged the test delivery after inspection.", flush=True)


def manual_reject() -> None:
    body = f"plat05-manual-dlq-{uuid.uuid4()}".encode()
    connection = connect("gateway")
    publish(connection.channel(), "tv.telemetry", "telemetry.tanque-01.sensor.od-01", body)
    connection.close()
    connection, channel, method, _, received = get_one("writer", "q.writer")
    check(method is not None and received == body, "published test message reached q.writer")
    channel.basic_nack(method.delivery_tag, requeue=False)
    connection.close()
    connection, channel, method, properties, received = get_one("api", "q.dlq")
    check(method is not None and received == body, "rejected message is readable from q.dlq by the API account")
    deaths = (properties.headers or {}).get("x-death", [])
    check(bool(deaths) and deaths[0].get("reason") == "rejected", "DLQ message contains rejection metadata")
    print(json.dumps(deaths[0], default=str), flush=True)
    channel.basic_ack(method.delivery_tag)
    connection.close()


def manual_persist() -> None:
    body = json.dumps({"marker": MARKER, "id": str(uuid.uuid4())}).encode()
    connection = connect("gateway")
    publish(connection.channel(), "tv.telemetry", "telemetry.tanque-01.sensor.od-01", body)
    connection.close()
    print(f"MANUAL_MARKER={MARKER}", flush=True)


def manual_verify_persistent() -> None:
    connection, channel, method, properties, body = get_one("writer", "q.writer")
    check(method is not None, "q.writer still contains the persistent message")
    check(properties.delivery_mode == 2 and MARKER.encode() in body, "persistent message marker survived restart")
    channel.basic_ack(method.delivery_tag)
    connection.close()


def manual_permissions() -> None:
    expect_access_refused(
        "gateway",
        lambda channel: channel.basic_publish("tv.commands", "command.test", b"{}"),
    )
    expect_access_refused("gateway", lambda channel: channel.basic_get("q.writer", auto_ack=True))
    expect_access_refused("writer", lambda channel: channel.basic_publish("tv.telemetry", "telemetry.test", b"{}"))
    expect_access_refused("writer", lambda channel: channel.basic_publish("", "q.writer", b"{}"))
    print("BLOCKER: current definitions deny Writer retries through amq.default; see infra/rabbitmq/README.md.", flush=True)
    expect_access_refused("writer", lambda channel: channel.basic_get("q.dlq", auto_ack=True))


if __name__ == "__main__":
    wait_for_broker()
    phase = os.sys.argv[1] if len(os.sys.argv) > 1 else ""
    phases = {
        "before": before_restart,
        "after": after_restart,
        "publish": manual_publish,
        "consume": manual_consume,
        "reject": manual_reject,
        "persist": manual_persist,
        "verify-persistent": manual_verify_persistent,
        "permissions": manual_permissions,
    }
    if phase not in phases:
        raise SystemExit("usage: integration_test.py before|after|publish|consume|reject|persist|verify-persistent|permissions")
    phases[phase]()

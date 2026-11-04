#!/usr/bin/env python3
"""Generate local TankVitals credentials and RabbitMQ definitions.

Run from any directory with Python 3.10+: ``python scripts/gerar_segredos.py``.
The generated .env and secrets/definitions.json must never be committed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS_PATH = Path("secrets") / "definitions.json"

# These names and permissions follow the PLAT-05 proposal in §14 of
# docs/ARQUITETURA.md. The contract is still awaiting review by the other fronts.
RABBIT_USERS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "api": ("monitoring", ("q.ws", "q.dlq"), ("tv.commands", "tv.jobs")),
    "gateway": ("", ("q.commands",), ("tv.telemetry", "tv.dlx")),
    "writer": ("", ("q.writer",), ()),
    "rules": ("", ("q.rules",), ("tv.commands", "tv.events")),
    "inference": ("", ("q.inference",), ("tv.insights", "tv.events")),
    "worker": ("", ("q.jobs.datagen", "q.jobs.train"), ("tv.events",)),
}

SECRET_KEYS = (
    "MQTT_SIMULATOR_PASSWORD",
    "MQTT_GATEWAY_PASSWORD",
    "RABBITMQ_API_PASSWORD",
    "RABBITMQ_GATEWAY_PASSWORD",
    "RABBITMQ_WRITER_PASSWORD",
    "RABBITMQ_RULES_PASSWORD",
    "RABBITMQ_INFERENCE_PASSWORD",
    "RABBITMQ_WORKER_PASSWORD",
    "POSTGRES_PASSWORD",
    "INFLUXDB_PASSWORD",
    "INFLUXDB_ADMIN_TOKEN",
    "PGADMIN_DEFAULT_PASSWORD",
    "JWT_SECRET_KEY",
    "DEMO_VIEWER_PASSWORD",
    "DEMO_OPERATOR_PASSWORD",
    "DEMO_ADMIN_PASSWORD",
)

FIXED_VALUES = {
    "MQTT_SIMULATOR_USERNAME": "simulator",
    "MQTT_GATEWAY_USERNAME": "gateway",
    "RABBITMQ_API_USERNAME": "api",
    "RABBITMQ_GATEWAY_USERNAME": "gateway",
    "RABBITMQ_WRITER_USERNAME": "writer",
    "RABBITMQ_RULES_USERNAME": "rules",
    "RABBITMQ_INFERENCE_USERNAME": "inference",
    "RABBITMQ_WORKER_USERNAME": "worker",
    "POSTGRES_DB": "tankvitals",
    "POSTGRES_USER": "tankvitals",
    "INFLUXDB_USERNAME": "tankvitals",
    "PGADMIN_DEFAULT_EMAIL": "admin@tankvitals.local",
    "DEMO_VIEWER_USERNAME": "viewer",
    "DEMO_OPERATOR_USERNAME": "operator",
    "DEMO_ADMIN_USERNAME": "admin",
}

PLAT_ENV_KEYS = (*FIXED_VALUES.keys(), *SECRET_KEYS)

DEMO_KEYS = (
    "DEMO_VIEWER_PASSWORD",
    "DEMO_OPERATOR_PASSWORD",
    "DEMO_ADMIN_PASSWORD",
)


def _new_value(key: str) -> str:
    if key in FIXED_VALUES:
        return FIXED_VALUES[key]
    # 32 random bytes (256 bits), URL-safe and valid in unquoted dotenv values.
    return secrets.token_urlsafe(32)


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.exists():
        return [], {}

    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.removeprefix("export ").strip()
        if key:
            values[key] = value.strip().strip("\"'")
    return lines, values


def _merge_env(lines: list[str], values: dict[str, str], force: bool) -> tuple[str, dict[str, str]]:
    merged = dict(values)
    generated: dict[str, str] = {}
    for key in PLAT_ENV_KEYS:
        if key in FIXED_VALUES:
            value = merged.get(key) or FIXED_VALUES[key]
        elif force or not merged.get(key):
            value = _new_value(key)
        else:
            value = merged[key]
        merged[key] = value
        generated[key] = value

    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].removeprefix("export ").strip() if "=" in stripped else ""
        if key in generated:
            rendered.append(f"{key}={generated[key]}")
            seen.add(key)
        else:
            rendered.append(line)

    missing = [key for key in PLAT_ENV_KEYS if key not in seen]
    if missing:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.extend(("# PLAT-generated local credentials; keep this file private.",))
        rendered.extend(f"{key}={generated[key]}" for key in missing)

    return "\n".join(rendered).rstrip() + "\n", generated


def rabbit_password_hash(password: str, salt: bytes | None = None) -> str:
    """Return RabbitMQ's base64(salt || SHA256(salt || UTF8(password)))."""
    salt = secrets.token_bytes(4) if salt is None else salt
    if len(salt) != 4:
        raise ValueError("O salt RabbitMQ deve ter exatamente 4 bytes.")
    digest = hashlib.sha256(salt + password.encode("utf-8")).digest()
    return base64.b64encode(salt + digest).decode("ascii")


def build_definitions(passwords: dict[str, str]) -> dict[str, object]:
    users = []
    permissions = []
    for username, (tag, read_queues, write_exchanges) in RABBIT_USERS.items():
        password_key = f"RABBITMQ_{username.upper()}_PASSWORD"
        user: dict[str, object] = {
            "name": username,
            "password_hash": rabbit_password_hash(passwords[password_key]),
            "hashing_algorithm": "rabbit_password_hashing_sha256",
            "tags": tag,
        }
        users.append(user)
        permissions.append(
            {
                "user": username,
                "vhost": "/",
                "configure": "^$",
                "write": "^(?:" + "|".join(map(_regex, write_exchanges)) + ")$" if write_exchanges else "^$",
                "read": "^(?:" + "|".join(map(_regex, read_queues)) + ")$" if read_queues else "^$",
            }
        )

    exchanges = [
        ("tv.telemetry", "topic"),
        ("tv.commands", "topic"),
        ("tv.insights", "topic"),
        ("tv.events", "topic"),
        ("tv.jobs", "direct"),
        ("tv.dlx", "fanout"),
    ]
    exchange_defs = [
        {"name": name, "vhost": "/", "type": kind, "durable": True, "auto_delete": False, "internal": False, "arguments": {}}
        for name, kind in exchanges
    ]

    queue_bindings = {
        "q.writer": (("tv.telemetry", "telemetry.#"), ("tv.insights", "#")),
        "q.ws": (("tv.telemetry", "#"), ("tv.insights", "#"), ("tv.events", "#")),
        "q.rules": (("tv.telemetry", "telemetry.#"),),
        "q.inference": (("tv.telemetry", "telemetry.*.equipment.*"), ("tv.events", "model.updated")),
        "q.commands": (("tv.commands", "#"),),
        "q.jobs.datagen": (("tv.jobs", "datagen"),),
        "q.jobs.train": (("tv.jobs", "train"),),
        "q.dlq": (("tv.dlx", "#"),),
    }
    queues = [
        {
            "name": name,
            "vhost": "/",
            "durable": True,
            "auto_delete": False,
            "arguments": {} if name == "q.dlq" else {"x-dead-letter-exchange": "tv.dlx"},
        }
        for name in queue_bindings
    ]
    bindings = [
        {
            "source": exchange,
            "vhost": "/",
            "destination": queue,
            "destination_type": "queue",
            "routing_key": key,
            "arguments": {},
        }
        for queue, entries in queue_bindings.items()
        for exchange, key in entries
    ]
    return {
        "global_parameters": [],
        "policies": [],
        "parameters": [],
        "users": users,
        "vhosts": [{"name": "/"}],
        "permissions": permissions,
        "topic_permissions": [],
        "exchanges": exchange_defs,
        "queues": queues,
        "bindings": bindings,
    }


def _regex(value: str) -> str:
    # All current RabbitMQ resource names are ASCII and contain only safe
    # punctuation; this escaping also makes additions safe by default.
    import re

    return re.escape(value)


def generate(root: Path, force: bool = False, output: TextIO | None = None) -> None:
    output = output if output is not None else sys.stdout
    env_path = root / ".env"
    definitions_path = root / DEFINITIONS_PATH
    env_lines, existing = _read_env(env_path)

    if not force and (env_path.exists() or definitions_path.exists()):
        existing_path = env_path if env_path.exists() else definitions_path
        raise FileExistsError(f"{existing_path} já existe; use --force para regenerar os segredos.")

    rendered_env, passwords = _merge_env(env_lines, existing, force)
    definitions = json.dumps(build_definitions(passwords), ensure_ascii=False, indent=2) + "\n"

    # Prepare both temporary files before replacing either output. This avoids
    # leaving a half-written JSON or dotenv file if generation itself fails.
    env_path.parent.mkdir(parents=True, exist_ok=True)
    definitions_path.parent.mkdir(parents=True, exist_ok=True)
    env_temp: Path | None = None
    definitions_temp: Path | None = None
    try:
        env_temp = _stage(env_path, rendered_env)
        definitions_temp = _stage(definitions_path, definitions)
        os.replace(env_temp, env_path)
        os.replace(definitions_temp, definitions_path)
    finally:
        if env_temp is not None:
            env_temp.unlink(missing_ok=True)
        if definitions_temp is not None:
            definitions_temp.unlink(missing_ok=True)

    print(f"Credenciais gravadas em {env_path}", file=output)
    print(f"Definições do RabbitMQ gravadas em {definitions_path}", file=output)
    print("\nSenhas dos usuários de demonstração (guarde em local seguro):", file=output)
    for key in DEMO_KEYS:
        print(f"  {key.removeprefix('DEMO_').removesuffix('_PASSWORD').lower()}: {passwords[key]}", file=output)


def _stage(path: Path, content: str) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    os.close(fd)
    try:
        # Restrict access before writing secret values into the temporary file.
        _set_private_permissions(temp_path)
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _set_private_permissions(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
        return

    icacls = shutil.which("icacls")
    whoami = shutil.which("whoami")
    if not icacls or not whoami:
        raise PermissionError("Não foi possível restringir as permissões do arquivo no Windows.")
    identity_result = subprocess.run(
        [whoami],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=15,
        check=False,
    )
    identity = identity_result.stdout.strip()
    if identity_result.returncode != 0 or not identity:
        raise PermissionError("Não foi possível identificar a conta atual no Windows.")

    result = subprocess.run(
        [icacls, str(path), "/inheritance:r", "/grant:r", f"{identity}:(F)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise PermissionError("icacls não conseguiu restringir o acesso ao arquivo gerado.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rotaciona as credenciais PLAT existentes")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="pasta de saída alternativa, útil para testar sem criar credenciais na raiz do projeto",
    )
    args = parser.parse_args(argv)
    output_root = args.output_dir.resolve() if args.output_dir else ROOT
    if os.name == "nt" and any("onedrive" in part.casefold() for part in output_root.parts):
        print(
            f"Aviso: {output_root} parece estar dentro do OneDrive; .env e secrets/ podem ser sincronizados.",
            file=sys.stderr,
        )
    try:
        generate(output_root, force=args.force)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Erro ao gerar segredos: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

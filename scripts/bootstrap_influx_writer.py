#!/usr/bin/env python3
"""Create a bucket-write-only InfluxDB token for PLAT-06 Writer.

The administrative token is read from INFLUXDB_ADMIN_TOKEN in the process
environment or the repository .env. The created token is written only to the
ignored secrets/ directory and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = Path("secrets") / "influx_writer.token"
TOKEN_DESCRIPTION = "tankvitals-plat-writer"


class BootstrapError(RuntimeError):
    """An expected configuration or InfluxDB API error without secret values."""


def _dotenv_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.removeprefix("export ").strip() == key:
            return value.strip().strip("\"'")
    return None


def _api_json(base_url: str, admin_token: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Token {admin_token}", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url.rstrip('/')}{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
    except HTTPError as exc:
        # Never include request headers, response body, or tokens in diagnostics.
        raise BootstrapError(f"InfluxDB API returned HTTP {exc.code} for {method} {path.split('?')[0]}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"InfluxDB API request failed for {method} {path.split('?')[0]}") from None
    if not isinstance(data, dict):
        raise BootstrapError("InfluxDB API returned an invalid JSON object")
    return data


def _single(items: Any, label: str, expected_name: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise BootstrapError(f"InfluxDB did not return a {label} list")
    matches = [item for item in items if isinstance(item, dict) and item.get("name") == expected_name]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise BootstrapError(f"Expected exactly one InfluxDB {label} named {expected_name!r}")
    return matches[0]


def _write_private_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o600)
        _restrict_token_file(temp_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _restrict_token_file(path: Path) -> None:
    """Restrict Windows ACLs too; Windows chmod alone does not enforce mode 0600."""
    if os.name != "nt":
        return
    username = os.getenv("USERNAME")
    if not username:
        raise BootstrapError("Could not determine the current Windows account for token-file ACLs")
    domain = os.getenv("USERDOMAIN")
    account = f"{domain}\\{username}" if domain else username
    result = subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{account}:(R,W)",
            "*S-1-5-18:(F)",  # Local System
            "*S-1-5-32-544:(F)",  # Local Administrators
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapError("Could not restrict Windows permissions on the Writer token file")


def bootstrap_writer_token(
    *,
    base_url: str,
    org_name: str,
    bucket_name: str,
    admin_token: str,
    token_path: Path,
) -> None:
    if not admin_token.strip():
        raise BootstrapError("INFLUXDB_ADMIN_TOKEN is required")
    if token_path.exists():
        raise BootstrapError(f"Refusing to overwrite existing token file: {token_path}")

    orgs = _api_json(base_url, admin_token, "GET", f"/api/v2/orgs?{urlencode({'org': org_name})}")
    org = _single(orgs.get("orgs"), "organization", org_name)
    buckets = _api_json(
        base_url,
        admin_token,
        "GET",
        f"/api/v2/buckets?{urlencode({'orgID': org['id'], 'name': bucket_name})}",
    )
    bucket = _single(buckets.get("buckets"), "bucket", bucket_name)
    permissions = [
        {
            "action": "write",
            "resource": {"type": "buckets", "id": bucket["id"], "orgID": org["id"]},
        }
    ]
    authorization = _api_json(
        base_url,
        admin_token,
        "POST",
        "/api/v2/authorizations",
        {"orgID": org["id"], "description": TOKEN_DESCRIPTION, "permissions": permissions},
    )
    token = authorization.get("token")
    if not isinstance(token, str) or not token:
        authorization_id = authorization.get("id")
        if isinstance(authorization_id, str) and authorization_id:
            _api_json(base_url, admin_token, "DELETE", f"/api/v2/authorizations/{authorization_id}")
        raise BootstrapError("InfluxDB did not return the newly created Writer token")
    try:
        _write_private_token(token_path, token)
    except Exception as exc:
        authorization_id = authorization.get("id")
        if isinstance(authorization_id, str) and authorization_id:
            try:
                _api_json(base_url, admin_token, "DELETE", f"/api/v2/authorizations/{authorization_id}")
            except BootstrapError:
                raise BootstrapError("Could not store the Writer token or revoke its authorization") from None
        raise BootstrapError("Could not securely store the Writer token; authorization cleanup was attempted") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("INFLUX_URL", "http://localhost:8086"))
    parser.add_argument("--org", default=os.getenv("INFLUX_ORG", "unifacef"))
    parser.add_argument("--bucket", default=os.getenv("INFLUX_BUCKET", "tankvitals"))
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()

    admin_token = os.getenv("INFLUXDB_ADMIN_TOKEN") or _dotenv_value(args.root / ".env", "INFLUXDB_ADMIN_TOKEN")
    if not admin_token:
        print("ERROR: set INFLUXDB_ADMIN_TOKEN in the environment or repository .env", file=sys.stderr)
        return 2
    token_path = args.root / DEFAULT_TOKEN_FILE
    try:
        bootstrap_writer_token(
            base_url=args.url,
            org_name=args.org,
            bucket_name=args.bucket,
            admin_token=admin_token,
            token_path=token_path,
        )
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Writer-scoped token saved to {token_path}; its value was not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

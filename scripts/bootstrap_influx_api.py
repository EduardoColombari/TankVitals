"""Create the bucket-scoped read/write token used by the API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from bootstrap_influx_writer import BootstrapError, _api_json, _single, _write_private_token


def main() -> int:
    from urllib.parse import urlencode

    base_url = os.getenv("INFLUX_URL", "http://influxdb:8086")
    org_name = os.getenv("INFLUX_ORG", "unifacef")
    bucket_name = os.getenv("INFLUX_BUCKET", "tankvitals")
    admin_token = os.getenv("INFLUXDB_ADMIN_TOKEN", "")
    token_path = Path(os.getenv("INFLUX_API_TOKEN_FILE", "/run/plat06/secrets/influx_api.token"))
    try:
        orgs = _api_json(base_url, admin_token, "GET", f"/api/v2/orgs?{urlencode({'org': org_name})}")
        org = _single(orgs.get("orgs"), "organization", org_name)
        buckets = _api_json(base_url, admin_token, "GET", f"/api/v2/buckets?{urlencode({'orgID': org['id'], 'name': bucket_name})}")
        bucket = _single(buckets.get("buckets"), "bucket", bucket_name)
        permissions = [{"action": action, "resource": {"type": "buckets", "id": bucket["id"], "orgID": org["id"]}}
                       for action in ("read", "write")]
        auth = _api_json(base_url, admin_token, "POST", "/api/v2/authorizations",
                         {"orgID": org["id"], "description": "tankvitals-plat-api", "permissions": permissions})
        token = auth.get("token")
        if not isinstance(token, str) or not token:
            raise BootstrapError("InfluxDB did not return the API token")
        _write_private_token(token_path, token)
    except Exception as exc:
        print(f"ERROR: Could not create bucket-scoped API token ({type(exc).__name__})", file=sys.stderr)
        return 1
    print("Bucket-scoped API token created; its value was not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# PLAT-06 Writer token

The Writer uses a dedicated InfluxDB authorization with **write access to the
`tankvitals` bucket only**. It does not use the setup/admin token. The bootstrap
script reads `INFLUXDB_ADMIN_TOKEN` from the process environment or the root
`.env`, creates the scoped authorization through the InfluxDB API, and writes
the token to the ignored `secrets/influx_writer.token` file without printing
its value.

From the repository root, after InfluxDB is initialized with organization
`unifacef` and bucket `tankvitals`:

```powershell
python scripts/bootstrap_influx_writer.py
```

Configure the Writer process with:

```text
INFLUX_TOKEN_FILE=/run/secrets/influx_writer.token
```

Mount `secrets/influx_writer.token` at that container path as a read-only
secret. The Writer accepts `INFLUX_TOKEN` for backwards compatibility, but the
scoped token file is the PLAT deployment path. The bootstrap refuses to
overwrite an existing token file; revoke the previous authorization in
InfluxDB before rotating and moving the old file out of the way.

Manual checks in PowerShell should verify that the token can write line
protocol to `tankvitals` (HTTP 204) and that a query or bucket-list request is
denied. Do not print the token or include it in command arguments or logs.
These live permission checks still require an accessible InfluxDB service.

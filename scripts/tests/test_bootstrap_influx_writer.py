"""Unit checks for the PLAT Writer's least-privilege InfluxDB token bootstrap."""

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from scripts.bootstrap_influx_writer import BootstrapError, bootstrap_writer_token


class InfluxWriterTokenBootstrapTests(unittest.TestCase):
    def test_creates_write_only_bucket_authorization_and_saves_token_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "secrets" / "influx_writer.token"
            responses = [
                {"orgs": [{"id": "org-id", "name": "unifacef"}]},
                {"buckets": [{"id": "bucket-id", "name": "tankvitals"}]},
                {"id": "auth-id", "token": "writer-secret-value"},
            ]
            with patch("scripts.bootstrap_influx_writer._api_json", side_effect=responses) as api, patch(
                "scripts.bootstrap_influx_writer._restrict_token_file"
            ) as restrict_acl:
                bootstrap_writer_token(
                    base_url="http://influx.test:8086",
                    org_name="unifacef",
                    bucket_name="tankvitals",
                    admin_token="admin-secret-value",
                    token_path=token_path,
                )

            self.assertEqual(token_path.read_text(encoding="utf-8"), "writer-secret-value\n")
            restrict_acl.assert_called_once()
            create_call = api.call_args_list[2]
            self.assertEqual(create_call.args[2], "POST")
            body = create_call.args[4]
            self.assertEqual(body["permissions"], [{
                "action": "write",
                "resource": {"type": "buckets", "id": "bucket-id", "orgID": "org-id"},
            }])
            self.assertNotIn("read", str(body["permissions"]))

    def test_windows_token_acl_grants_only_current_user_and_system_admin(self):
        from scripts import bootstrap_influx_writer as bootstrap

        completed = type("Completed", (), {"returncode": 0})()
        with patch("scripts.bootstrap_influx_writer.os.name", "nt"), patch.dict(
            os.environ, {"USERNAME": "TestUser", "USERDOMAIN": "TestDomain"}
        ), patch("scripts.bootstrap_influx_writer.subprocess.run", return_value=completed) as run:
            bootstrap._restrict_token_file(Path("private-token"))
        args = run.call_args.args[0]
        self.assertIn("TestDomain\\TestUser:(R,W)", args)
        self.assertIn("*S-1-5-18:(F)", args)
        self.assertIn("*S-1-5-32-544:(F)", args)
        self.assertIn("/inheritance:r", args)

    def test_refuses_to_overwrite_an_existing_token_without_calling_influx(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "writer.token"
            token_path.write_text("existing-token\n", encoding="utf-8")
            with patch("scripts.bootstrap_influx_writer._api_json") as api:
                with self.assertRaisesRegex(BootstrapError, "Refusing to overwrite"):
                    bootstrap_writer_token(
                        base_url="http://influx.test:8086",
                        org_name="unifacef",
                        bucket_name="tankvitals",
                        admin_token="admin-secret-value",
                        token_path=token_path,
                    )
            api.assert_not_called()

    def test_rejects_missing_bucket_without_creating_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "writer.token"
            with patch("scripts.bootstrap_influx_writer._api_json", side_effect=[
                {"orgs": [{"id": "org-id", "name": "unifacef"}]},
                {"buckets": []},
            ]) as api:
                with self.assertRaisesRegex(BootstrapError, "bucket named"):
                    bootstrap_writer_token(
                        base_url="http://influx.test:8086",
                        org_name="unifacef",
                        bucket_name="tankvitals",
                        admin_token="admin-secret-value",
                        token_path=token_path,
                    )
            self.assertEqual(api.call_count, 2)
            self.assertFalse(token_path.exists())

    def test_revokes_authorization_if_private_token_storage_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "writer.token"
            with patch("scripts.bootstrap_influx_writer._api_json", side_effect=[
                {"orgs": [{"id": "org-id", "name": "unifacef"}]},
                {"buckets": [{"id": "bucket-id", "name": "tankvitals"}]},
                {"id": "auth-id", "token": "writer-secret-value"},
                {},
            ]) as api, patch("scripts.bootstrap_influx_writer._write_private_token", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(BootstrapError, "authorization cleanup was attempted"):
                    bootstrap_writer_token(
                        base_url="http://influx.test:8086",
                        org_name="unifacef",
                        bucket_name="tankvitals",
                        admin_token="admin-secret-value",
                        token_path=token_path,
                    )
            self.assertEqual(api.call_count, 4)
            self.assertEqual(api.call_args_list[3].args[2:4], ("DELETE", "/api/v2/authorizations/auth-id"))


if __name__ == "__main__":
    unittest.main()

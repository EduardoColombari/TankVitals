"""Unit tests for the standalone PLAT secret generator."""

import base64
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.gerar_segredos import (
    PLAT_ENV_KEYS,
    SECRET_KEYS,
    build_definitions,
    generate,
    main,
    rabbit_password_hash,
)


class SecretGeneratorTests(unittest.TestCase):
    def test_rabbitmq_password_hash_matches_published_sha256_example(self):
        self.assertEqual(
            rabbit_password_hash("test12", bytes.fromhex("908dc60a")),
            "kI3GCqW5JLMJa4iX1lo7X4D6XbYqlLgxIs30+P6tENUV2POR",
        )

    def test_builds_service_users_and_durable_queue_topology(self):
        passwords = {
            f"RABBITMQ_{name.upper()}_PASSWORD": f"test-{name}"
            for name in ("api", "gateway", "writer", "rules", "inference", "worker")
        }
        definitions = build_definitions(passwords)

        self.assertEqual(
            {user["name"] for user in definitions["users"]},
            {"api", "gateway", "writer", "rules", "inference", "worker"},
        )
        self.assertEqual(
            {queue["name"] for queue in definitions["queues"]},
            {"q.writer", "q.ws", "q.rules", "q.inference", "q.commands", "q.jobs.datagen", "q.jobs.train", "q.dlq"},
        )
        self.assertEqual(
            next(user for user in definitions["users"] if user["name"] == "api")["tags"],
            "monitoring",
        )
        api_user = next(user for user in definitions["users"] if user["name"] == "api")
        self.assertNotIn("password", api_user)
        self.assertEqual(len(base64.b64decode(api_user["password_hash"])), 36)
        api_permissions = next(item for item in definitions["permissions"] if item["user"] == "api")
        self.assertEqual(api_permissions["read"], "^(?:q\\.ws|q\\.dlq)$")
        for queue in definitions["queues"]:
            if queue["name"] == "q.dlq":
                self.assertEqual(queue["arguments"], {})
            else:
                self.assertEqual(queue["arguments"]["x-dead-letter-exchange"], "tv.dlx")

    def test_refuses_preexisting_outputs_without_force(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env_path = root / ".env"
            env_path.write_text("APP_MODE=local\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                generate(root, output=io.StringIO())
            self.assertEqual(env_path.read_text(encoding="utf-8"), "APP_MODE=local\n")

    def test_initial_generation_creates_private_outputs_and_expected_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "TankVitals é com espaço"
            root.mkdir()
            output = io.StringIO()

            generate(root, output=output)

            env_path = root / ".env"
            definitions_path = root / "secrets" / "definitions.json"
            env = dict(
                line.split("=", 1)
                for line in env_path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
            )
            definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
            self.assertTrue(definitions_path.parent.is_dir())
            self.assertTrue(set(PLAT_ENV_KEYS).issubset(env))
            self.assertEqual(env["MQTT_SIMULATOR_USERNAME"], "simulator")
            self.assertEqual(env["MQTT_GATEWAY_USERNAME"], "gateway")
            self.assertEqual(env["DEMO_ADMIN_USERNAME"], "admin")
            for key in SECRET_KEYS:
                self.assertGreaterEqual(len(env[key]), 43, key)

            logged = output.getvalue()
            for key in SECRET_KEYS:
                if not key.startswith("DEMO_"):
                    self.assertNotIn(env[key], logged)
            for key in ("DEMO_VIEWER_PASSWORD", "DEMO_OPERATOR_PASSWORD", "DEMO_ADMIN_PASSWORD"):
                self.assertIn(env[key], logged)
            for user in definitions["users"]:
                service_key = f"RABBITMQ_{user['name'].upper()}_PASSWORD"
                self.assertNotIn(env[service_key], json.dumps(user))

            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(definitions_path.stat().st_mode), 0o600)

    def test_invalid_env_encoding_fails_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_bytes(b"\xff\xfe")
            with self.assertRaises(UnicodeError):
                generate(root, force=True, output=io.StringIO())
            self.assertFalse((root / "secrets").exists())

    def test_cli_supports_an_isolated_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                result = main(["--output-dir", folder])
            self.assertEqual(result, 0)
            self.assertTrue((Path(folder) / ".env").is_file())
            self.assertTrue((Path(folder) / "secrets" / "definitions.json").is_file())
            env_text = (Path(folder) / ".env").read_text(encoding="utf-8")
            for line in env_text.splitlines():
                if line.startswith("RABBITMQ_") and "_PASSWORD=" in line:
                    self.assertNotIn(line.split("=", 1)[1], output.getvalue())

    def test_force_preserves_unrelated_settings_and_rotates_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text("APP_MODE=local\n", encoding="utf-8")
            output = io.StringIO()
            generate(root, force=True, output=output)
            env_text = (root / ".env").read_text(encoding="utf-8")
            definitions = json.loads((root / "secrets" / "definitions.json").read_text(encoding="utf-8"))
            api_password = next(line.split("=", 1)[1] for line in env_text.splitlines() if line.startswith("RABBITMQ_API_PASSWORD="))
            self.assertIn("APP_MODE=local", env_text)
            self.assertNotIn(api_password, json.dumps(definitions))
            self.assertIn("viewer:", output.getvalue())
            old_jwt = next(line.split("=", 1)[1] for line in env_text.splitlines() if line.startswith("JWT_SECRET_KEY="))
            generate(root, force=True, output=io.StringIO())
            rotated_text = (root / ".env").read_text(encoding="utf-8")
            new_jwt = next(line.split("=", 1)[1] for line in rotated_text.splitlines() if line.startswith("JWT_SECRET_KEY="))
            self.assertNotEqual(old_jwt, new_jwt)

    def test_refuses_implicit_rotation_and_force_updates_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generate(root, output=io.StringIO())
            before = (root / ".env").read_text(encoding="utf-8")
            with self.assertRaises(FileExistsError):
                generate(root, output=io.StringIO())

            generate(root, force=True, output=io.StringIO())
            after = (root / ".env").read_text(encoding="utf-8")
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()

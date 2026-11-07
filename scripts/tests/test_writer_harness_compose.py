"""Regression checks for the isolated PLAT-06 Compose image build targets."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/rabbitmq/plat05/writer-integration.compose.yaml"
RUNNER = ROOT / "scripts/tests/test_writer_influx_integration.ps1"
CLIENT = ROOT / "infra/rabbitmq/plat05/writer_influx_test.py"


def service_block(compose_text: str, name: str) -> str:
    services = compose_text.split("services:", 1)[1].split("\nvolumes:", 1)[0]
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)", services)
    if not match:
        raise AssertionError(f"Compose service {name!r} was not found")
    return match.group(1)


class WriterComposeBuildRegressionTests(unittest.TestCase):
    def test_runner_builds_only_services_with_build_contexts(self):
        compose_text = COMPOSE.read_text(encoding="utf-8")
        runner_text = RUNNER.read_text(encoding="utf-8")
        token_bootstrap = service_block(compose_text, "token-bootstrap")
        fault_proxy = service_block(compose_text, "influx-fault-proxy")
        test_client = service_block(compose_text, "test-client")

        self.assertIn("\n    build:\n", token_bootstrap)
        self.assertIn("\n    build:\n", fault_proxy)
        self.assertNotIn("\n    build:\n", test_client)
        self.assertIn("image: ${COMPOSE_PROJECT_NAME:?Set unique COMPOSE_PROJECT_NAME}-test-tools", fault_proxy)
        self.assertIn("image: ${COMPOSE_PROJECT_NAME:?Set unique COMPOSE_PROJECT_NAME}-test-tools", test_client)
        self.assertIn(
            'Invoke-Compose @("build", "token-bootstrap", "influx-fault-proxy")',
            runner_text,
        )
        self.assertNotIn('Invoke-Compose @("build", "token-bootstrap", "influx-fault-proxy", "test-client")', runner_text)

    def test_test_client_does_not_restart_dependencies_and_keeps_script_entrypoint(self):
        runner_text = RUNNER.read_text(encoding="utf-8")

        self.assertIn('"run", "--rm", "--no-deps", "--entrypoint", "python"', runner_text)
        self.assertIn('"test-client", "/opt/plat06/writer_influx_test.py", $Mode', runner_text)
        self.assertIn('"Docker Compose run test-client $Mode"', runner_text)

    def test_runner_allows_docker_api_version_negotiation(self):
        runner_text = RUNNER.read_text(encoding="utf-8")

        self.assertIn('"DOCKER_API_VERSION"', runner_text)
        self.assertIn("Remove-Item Env:DOCKER_API_VERSION", runner_text)

    def test_scenarios_use_distinct_probe_values_except_restart_pair(self):
        client_text = CLIENT.read_text(encoding="utf-8")

        self.assertIn("uuid.uuid4().int & ((1 << 63) - 1)", client_text)
        self.assertIn('envelope(restart_probe=mode in {"publish-only", "drain"})', client_text)
        self.assertIn("wait_queue_ready(\"q.writer\")", client_text)
        self.assertIn("expected_id={event['id']}, actual_id={actual_id}", client_text)

    def test_writer_token_scope_uses_authorization_checks_not_bucket_list_status(self):
        client_text = CLIENT.read_text(encoding="utf-8")

        self.assertIn("Writer token cannot write outside tankvitals", client_text)
        self.assertIn("Writer token cannot read from tankvitals", client_text)
        self.assertNotIn("Writer token cannot list buckets", client_text)

    def test_scope_probe_is_discovered_with_admin_token_and_created_in_resolved_org(self):
        client_text = CLIENT.read_text(encoding="utf-8")

        self.assertIn('/api/v2/buckets?orgID={urllib.parse.quote(org[\'id\'])}&limit=1000', client_text)
        self.assertIn('token=ADMIN_TOKEN,', client_text)
        self.assertIn('"orgID": org["id"], "name": denied_bucket', client_text)
        self.assertIn('probe.get("orgID") != org["id"]', client_text)
        self.assertIn('probe.get("id")', client_text)

    def test_restart_starts_only_writer_without_evaluating_its_dependencies(self):
        runner_text = RUNNER.read_text(encoding="utf-8")

        self.assertIn('Invoke-Compose @("up", "-d", "--no-deps", "writer")', runner_text)
        self.assertIn('Invoke-TestClient "publish-only"', runner_text)
        self.assertIn('Invoke-TestClient "queue-pending"', runner_text)
        self.assertIn('Invoke-TestClient "drain"', runner_text)


if __name__ == "__main__":
    unittest.main()

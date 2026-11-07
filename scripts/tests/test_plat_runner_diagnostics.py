"""Regression checks for PLAT Docker runner diagnostics and API negotiation."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WRITER_RUNNER = ROOT / "scripts/tests/test_writer_influx_integration.ps1"
IMAGE_RUNNER = ROOT / "scripts/tests/validate_plat_images.ps1"


class PlatRunnerRegressionTests(unittest.TestCase):
    def test_writer_runner_does_not_force_docker_api_version(self):
        runner = WRITER_RUNNER.read_text(encoding="utf-8")

        self.assertIn('"DOCKER_API_VERSION"', runner)
        self.assertIn("Remove-Item Env:DOCKER_API_VERSION", runner)
        self.assertIn(
            'foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process") }',
            runner,
        )

    def test_image_runner_reports_nginx_test_output(self):
        runner = IMAGE_RUNNER.read_text(encoding="utf-8")

        self.assertIn("function Get-ProcessDetail($Result)", runner)
        self.assertIn("Get-ProcessDetail $nginxResult", runner)

    def test_image_runner_provides_backend_dns_and_checks_unavailable_api(self):
        runner = IMAGE_RUNNER.read_text(encoding="utf-8")

        self.assertIn('"network", "create", $network', runner)
        self.assertIn('"--network-alias", "backend"', runner)
        self.assertIn("($apiStatus -eq 502)", runner)
        self.assertIn('"network", "rm", $network', runner)


if __name__ == "__main__":
    unittest.main()

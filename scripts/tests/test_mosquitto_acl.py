"""Static checks for the isolated Mosquitto ACL proposal."""

import unittest
from pathlib import Path


ACL_PATH = Path(__file__).resolve().parents[2] / "infra" / "mosquitto" / "config" / "acl"


class MosquittoAclTests(unittest.TestCase):
    def test_simulator_and_gateway_have_opposite_topic_permissions(self):
        acl = ACL_PATH.read_text(encoding="utf-8")
        self.assertIn("user simulator\ntopic write tankvitals/+/+/+/telemetry", acl)
        self.assertIn("topic read tankvitals/+/+/+/cmd", acl)
        self.assertIn("topic write tankvitals/sim/status", acl)
        self.assertIn("user gateway\ntopic read tankvitals/+/+/+/telemetry", acl)
        self.assertIn("topic write tankvitals/+/+/+/cmd", acl)
        self.assertIn("topic read tankvitals/sim/status", acl)
        self.assertNotIn("topic readwrite", acl)
        self.assertNotIn("tankvitals/#", acl)


if __name__ == "__main__":
    unittest.main()

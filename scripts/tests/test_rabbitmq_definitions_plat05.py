"""PLAT-05 contract checks against the PLAT-02 definitions builder."""

import unittest

from scripts.gerar_segredos import build_definitions


class RabbitDefinitionsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        passwords = {
            f"RABBITMQ_{name.upper()}_PASSWORD": f"fixture-{name}"
            for name in ("api", "gateway", "writer", "rules", "inference", "worker")
        }
        cls.definitions = build_definitions(passwords)

    def test_expected_exchange_types_and_durable_queues(self):
        self.assertEqual(
            {item["name"]: item["type"] for item in self.definitions["exchanges"]},
            {
                "tv.telemetry": "topic",
                "tv.commands": "topic",
                "tv.insights": "topic",
                "tv.events": "topic",
                "tv.jobs": "direct",
                "tv.dlx": "fanout",
            },
        )
        queues = {item["name"]: item for item in self.definitions["queues"]}
        self.assertEqual(
            set(queues),
            {"q.writer", "q.ws", "q.rules", "q.inference", "q.commands", "q.jobs.datagen", "q.jobs.train", "q.dlq"},
        )
        self.assertTrue(all(item["durable"] for item in queues.values()))
        self.assertEqual(queues["q.dlq"]["arguments"], {})
        for name, queue in queues.items():
            if name != "q.dlq":
                self.assertEqual(queue["arguments"], {"x-dead-letter-exchange": "tv.dlx"})

    def test_bindings_route_writer_inference_jobs_and_dead_letters(self):
        bindings = {
            (item["source"], item["routing_key"], item["destination"])
            for item in self.definitions["bindings"]
        }
        self.assertTrue(
            {
                ("tv.telemetry", "telemetry.#", "q.writer"),
                ("tv.insights", "#", "q.writer"),
                ("tv.telemetry", "telemetry.*.equipment.*", "q.inference"),
                ("tv.events", "model.updated", "q.inference"),
                ("tv.jobs", "datagen", "q.jobs.datagen"),
                ("tv.jobs", "train", "q.jobs.train"),
                ("tv.dlx", "#", "q.dlq"),
            }.issubset(bindings)
        )

    def test_each_user_has_only_its_documented_queue_and_exchange_access(self):
        expected = {
            "api": ("^(?:q\\.ws|q\\.dlq)$", "^(?:tv\\.commands|tv\\.jobs)$", "monitoring"),
            "gateway": ("^(?:q\\.commands)$", "^(?:tv\\.telemetry|tv\\.dlx)$", ""),
            "writer": ("^(?:q\\.writer)$", "^$", ""),
            "rules": ("^(?:q\\.rules)$", "^(?:tv\\.commands|tv\\.events)$", ""),
            "inference": ("^(?:q\\.inference)$", "^(?:tv\\.insights|tv\\.events)$", ""),
            "worker": ("^(?:q\\.jobs\\.datagen|q\\.jobs\\.train)$", "^(?:tv\\.events)$", ""),
        }
        permissions = {item["user"]: item for item in self.definitions["permissions"]}
        users = {item["name"]: item for item in self.definitions["users"]}
        self.assertEqual(set(permissions), set(expected))
        self.assertEqual(set(users), set(expected))
        for username, (read, write, tag) in expected.items():
            with self.subTest(username=username):
                self.assertEqual(permissions[username]["read"], read)
                self.assertEqual(permissions[username]["write"], write)
                self.assertEqual(permissions[username]["configure"], "^$")
                self.assertEqual(users[username]["tags"], tag)
                self.assertNotIn("password", users[username])


if __name__ == "__main__":
    unittest.main()

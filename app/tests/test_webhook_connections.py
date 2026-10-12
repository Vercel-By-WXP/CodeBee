"""Webhook delivery transactions must release their SQLite handles."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import webhooks


class WebhookConnectionTests(unittest.TestCase):
    def test_claim_duplicate_and_release_close_connections(self):
        connections = []
        original_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as temporary:
            try:
                with patch.object(webhooks, "_FILE", Path(temporary) / "deliveries.json"):
                    with patch.object(webhooks.sqlite3, "connect", side_effect=connect):
                        self.assertTrue(webhooks.claim_delivery("delivery-1", now=1000)[0])
                        self.assertFalse(webhooks.claim_delivery("delivery-1", now=1001)[0])
                        webhooks.release_delivery("delivery-1")
                        self.assertTrue(webhooks.claim_delivery("delivery-1", now=1002)[0])
                self.assertEqual(len(connections), 4)
                for connection in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
            finally:
                for connection in connections:
                    connection.close()


if __name__ == "__main__":
    unittest.main()

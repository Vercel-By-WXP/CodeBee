import unittest

from scripts import borrow_scan_nightly as scan


class BorrowScanQueryContractTests(unittest.TestCase):
    def test_current_keywords_are_synced_to_permanent_and_rotating_queries(self):
        permanent = {query for _, query in scan.QUERIES}
        rotating = {
            query
            for batch in scan.BATCHES.values()
            for _, query in batch
        }

        self.assertIn("token+efficient+agent+OR+openclacky", permanent)
        self.assertIn("open-code-review+alternative+OR+codeai+review", permanent)
        self.assertIn("open-code-review+alternative+OR+codeai+review", rotating)
        self.assertIn("agentic+context+engineering+OR+evolving+playbook", rotating)
        self.assertIn("magicrew+OR+%E8%B6%85%E7%BA%A7%E9%BA%A6%E5%90%89", rotating)

    def test_encoded_unicode_and_existing_percent_escape_are_preserved(self):
        encoded = __import__("urllib.parse", fromlist=["quote"]).quote(
            "数字员工+OR+%E8%B6%85%E7%BA%A7", safe="+%"
        )

        self.assertEqual(encoded, "%E6%95%B0%E5%AD%97%E5%91%98%E5%B7%A5+OR+%E8%B6%85%E7%BA%A7")


if __name__ == "__main__":
    unittest.main()

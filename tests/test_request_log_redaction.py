import io
import logging
import unittest

from app.core.http_logging import log_request_exception, safe_request_target


class SafeRequestTargetTests(unittest.TestCase):
    def test_query_credentials_are_not_returned(self):
        self.assertEqual(
            safe_request_target("/api/state?token=sentinel&view=full"),
            "/api/state",
        )

    def test_preview_path_token_and_resource_name_are_redacted(self):
        self.assertEqual(
            safe_request_target("/preview/run-123/sentinel/private.md?token=other"),
            "/preview/<redacted>",
        )

    def test_regular_route_path_is_kept_without_query(self):
        self.assertEqual(
            safe_request_target("/api/tasks/task-123?limit=5"),
            "/api/tasks/task-123",
        )

    def test_exception_log_omits_sensitive_exception_text_and_traceback(self):
        stream = io.StringIO()
        logger = logging.getLogger("test.request_log_redaction")
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            try:
                raise ValueError("request token=exception-secret")
            except ValueError as exc:
                log_request_exception(
                    logger, "GET", "/api/state?token=query-secret", exc)
        finally:
            logger.removeHandler(handler)
            handler.close()
        output = stream.getvalue()
        self.assertIn("GET /api/state", output)
        self.assertIn("ValueError", output)
        self.assertNotIn("query-secret", output)
        self.assertNotIn("exception-secret", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()

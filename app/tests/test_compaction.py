import unittest
from unittest.mock import patch

from core import compaction, session_log


class ConversationCompactionTests(unittest.TestCase):
    def test_pressure_compaction_replaces_old_context_and_retains_recent_tail(self):
        session = session_log.Session("compaction-test")
        session.append("system_message", {"content": "system"})
        session.append("user_message", {"content": "old request " + "a" * 32000})
        session.append("assistant_message", {"content": "old response " + "b" * 12000})
        session.append("user_message", {"content": "recent request " + "c" * 8000})
        caller_inputs = []

        def summarize(messages):
            caller_inputs.append(messages)
            return "old context summary: preserve decisions and pending work"

        with patch.object(compaction.token_meter, "pressure_ratio", return_value=0.95):
            changed = compaction.maybe_compact(
                session, model="test-model", llm_caller=summarize,
                threshold=0.8, retain_tail_tokens=1000)

        self.assertTrue(changed)
        surface = session.derive_messages()
        self.assertEqual(surface[0]["role"], "system")
        self.assertTrue(any("old context summary" in row["content"] for row in surface))
        self.assertTrue(any("recent request" in row["content"] for row in surface))
        self.assertTrue(caller_inputs)
        event_types = [event.type for event in session.events()]
        self.assertIn("compaction_start", event_types)
        self.assertIn("compaction_end", event_types)


if __name__ == "__main__":
    unittest.main()

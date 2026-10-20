"""Prompt interpolation must not recursively consume user-authored tokens."""
import unittest

from app.core.prompt_templates import render_values


class PromptTemplateTests(unittest.TestCase):
    def test_replacement_values_are_inserted_once(self):
        rendered = render_values(
            "goal=__GOAL__; context=__CONTEXT__; text=__TEXT__", {
                "__GOAL__": "literal __CONTEXT__",
                "__CONTEXT__": "context value",
                "__TEXT__": "literal __GOAL__ and __UNKNOWN__",
            })

        self.assertEqual(
            rendered,
            "goal=literal __CONTEXT__; context=context value; "
            "text=literal __GOAL__ and __UNKNOWN__",
        )

    def test_unknown_template_token_remains_visible(self):
        self.assertEqual(render_values("__MISSING__", {}), "__MISSING__")


if __name__ == "__main__":
    unittest.main()

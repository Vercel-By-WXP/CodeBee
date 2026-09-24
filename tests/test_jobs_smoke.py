# -*- coding: utf-8 -*-
"""CLI smoke checks accept only the requested positive acknowledgement."""

from base import BaseTest


class TestCliSmokeResponse(BaseTest):
    def test_exact_acknowledgement_passes(self):
        from app.core.jobs import _smoke_response_ok
        self.assertTrue(_smoke_response_ok("OK\n"))
        self.assertTrue(_smoke_response_ok("startup banner\nOK.\n"))

    def test_negative_or_narrative_mentions_do_not_pass(self):
        from app.core.jobs import _smoke_response_ok
        self.assertFalse(_smoke_response_ok("NOT OK"))
        self.assertFalse(_smoke_response_ok("It will probably be OK"))
        self.assertFalse(_smoke_response_ok(""))

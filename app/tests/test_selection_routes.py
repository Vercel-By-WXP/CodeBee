import unittest
from unittest.mock import patch

import main
from core import selection_launch as selection


class SelectionRouteTests(unittest.TestCase):
    def handler(self, path, body=None):
        h = object.__new__(main.Handler)
        h.path = path
        h.headers = {}
        h._authed = lambda: True
        h._deny_control = lambda: None
        h._body = lambda: body
        h._json = lambda code, payload: (code, payload)
        return h

    def test_get_proposals_requires_authentication(self):
        h = self.handler('/api/tasks/t1/selection')
        h._authed = lambda: False
        with patch.object(selection, 'selection_info') as read:
            self.assertEqual(h._route_get()[0], 401)
        read.assert_not_called()

    def test_get_returns_only_reviewed_candidates(self):
        h = self.handler('/api/tasks/t1/selection')
        with patch.object(selection, 'selection_info', return_value={'run_id': 'r1'}) as read:
            self.assertEqual(h._route_get(), (200, {'run_id': 'r1'}))
        read.assert_called_once_with('t1')

    def test_post_obeys_control_gate_and_uses_normal_task_creation(self):
        h = self.handler('/api/tasks/t1/selection/serial', {'run_id': 'r1', 'candidate_id': 'c1'})
        h._deny_control = lambda: (423, {'error': 'held'})
        with patch.object(selection, 'launch_serial') as launch:
            self.assertEqual(h._route_post()[0], 423)
        launch.assert_not_called()
        h._deny_control = lambda: None
        with patch.object(selection, 'launch_serial', return_value=(200, {'task_id': 'child'})) as launch:
            self.assertEqual(h._route_post(), (200, {'task_id': 'child'}))
        self.assertEqual(launch.call_args.args[0:2], ('t1', {'run_id': 'r1', 'candidate_id': 'c1'}))
        self.assertEqual(launch.call_args.args[2], h._create_and_start)

    def test_invalid_package_is_reported_without_creating_task(self):
        h = self.handler('/api/tasks/t1/selection/serial', {})
        with patch.object(selection, 'launch_serial', side_effect=ValueError('not reviewed')):
            self.assertEqual(h._route_post(), (400, {'error': 'not reviewed'}))

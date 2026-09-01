import os
import tempfile
import unittest
from unittest.mock import patch

from utils import run_state


class RunStateTests(unittest.TestCase):
    def test_state_round_trip_is_atomic_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = os.path.join(temp_dir, "run_state.json")
            with patch.object(run_state.constants, "run_state_path", state_path):
                run_state.write_run_state("completed_empty", reason="no_source_configured")
                state = run_state.read_run_state()

            self.assertEqual(state["status"], "completed_empty")
            self.assertEqual(state["reason"], "no_source_configured")
            self.assertIn("updated_at", state)

    def test_missing_state_defaults_to_never_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = os.path.join(temp_dir, "missing.json")
            with patch.object(run_state.constants, "run_state_path", state_path):
                self.assertEqual(run_state.read_run_state(), {"status": "never_run"})

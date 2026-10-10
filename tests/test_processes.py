import subprocess
import types
import unittest
from unittest.mock import Mock, patch

from ytdlp_studio import processes


class ProcessTests(unittest.TestCase):
    def test_finished_process_needs_no_termination(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(processes.subprocess, "run") as run:
            processes.terminate_tree(process)
        run.assert_not_called()
        process.kill.assert_not_called()

    def test_windows_cancellation_kills_the_tree_without_a_window(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        with patch.object(processes, "os", types.SimpleNamespace(name="nt")), patch.object(processes.subprocess, "run") as run:
            processes.terminate_tree(process)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertIn("creationflags", run.call_args.kwargs)
        process.wait.assert_called_once_with(timeout=5)

    def test_taskkill_failure_has_a_bounded_fallback(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        with patch.object(processes, "os", types.SimpleNamespace(name="nt")), patch.object(processes.subprocess, "run", side_effect=OSError("Unavailable")):
            processes.terminate_tree(process)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_unix_cancellation_escalates_to_process_group_kill(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("child", 5), 0]
        system = types.SimpleNamespace(name="posix", killpg=Mock())
        signals = types.SimpleNamespace(SIGTERM=15, SIGKILL=9)
        with patch.object(processes, "os", system), patch.object(processes, "signal", signals):
            processes.terminate_tree(process)
            self.assertEqual(processes.window_kwargs(), {"start_new_session": True})
        self.assertEqual([call.args for call in system.killpg.call_args_list], [(123, 15), (123, 9)])

import subprocess
import os
import sys
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

from ytdlp_studio import processes


class ProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("spotapi"), "Windows Spotify discovery")
    def test_discovery_guards_native_shell_calls_before_dependency_import(self):
        script = '''
import os, sys, subprocess, _winapi
from unittest.mock import patch
from ytdlp_studio import catalogue
def audit(event, args):
    if event == 'os.system':
        raise AssertionError('Native shell call bypassed the hidden-process guard')
sys.addaudithook(audit)
original = _winapi.CreateProcess
launched = []
def hidden(*args, **kwargs):
    assert args[5] & subprocess.CREATE_NO_WINDOW, args
    launched.append(True)
    return original(*args, **kwargs)
def inspect(url):
    import spotapi.utils.logger
    assert os.system('') == 0
    assert os.system('   ') == 0
    assert os.system('exit /b 7') == 7
    child = 'import ctypes; raise SystemExit(bool(ctypes.windll.kernel32.GetConsoleWindow()))'
    command = subprocess.list2cmdline([sys.executable, '-c', child])
    assert os.system(command) == 0
    return {'title': 'Silent discovery'}
with patch.object(sys, 'argv', ['catalogue', 'inspect', 'fixture']), \\
     patch.object(catalogue, 'inspect_spotify', inspect), \\
     patch.object(_winapi, 'CreateProcess', hidden):
    assert catalogue.main() == 0
assert len(launched) >= 2
print('Discovery shell calls are hidden')
'''
        interpreters = [Path(sys.executable)]
        silent = Path(sys.executable).with_name("pythonw.exe")
        if silent.is_file():
            interpreters.append(silent)
        for interpreter in interpreters:
            with self.subTest(interpreter=interpreter.name):
                result = subprocess.run([str(interpreter), "-B", "-c", script], capture_output=True, text=True,
                                        timeout=25, **processes.window_kwargs())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Discovery shell calls are hidden", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows console behavior")
    def test_sync_and_preimported_asyncio_children_have_no_console(self):
        script = '''
import asyncio, ctypes, subprocess, sys
from ytdlp_studio.processes import hide_child_windows
hide_child_windows()
child = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
result = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NEW_CONSOLE)
assert result.returncode == 0 and result.stdout.strip() == '0', result
async def check():
    p = await asyncio.create_subprocess_exec(sys.executable, '-c', child,
        stdout=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NEW_CONSOLE)
    output, _ = await p.communicate()
    assert p.returncode == 0 and output.strip() == b'0', output
asyncio.run(check())
print('hidden sync and async children verified')
'''
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True,
                                timeout=20, **processes.window_kwargs())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("verified", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows process ownership")
    def test_job_object_kills_grandchild_after_parent_has_exited(self):
        import ctypes
        from ctypes import wintypes
        script = "import subprocess,sys; sys.stdin.readline(); p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], creationflags=subprocess.CREATE_NO_WINDOW); print(p.pid,flush=True)"
        parent = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, **processes.window_kwargs())
        tree = processes.own_process_tree(parent)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(parent.stdin.close)
        self.addCleanup(parent.kill)
        self.assertIsNotNone(tree, "Could not own the download process tree")
        self.addCleanup(tree.close)
        parent.stdin.write("start\n")
        parent.stdin.flush()
        pid = int(parent.stdout.readline())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        self.assertTrue(handle)
        self.addCleanup(kernel.CloseHandle, handle)
        parent.wait(timeout=10)
        processes.terminate_tree(parent, tree)
        self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)

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

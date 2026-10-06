import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ticktick_mcp_auth import AuthError, State
from ticktick_mcp_store import KeychainStore, MissingState

TOKEN = "synthetic-private-access"
REFRESH = "synthetic-private-refresh"


class MCPStoreTest(unittest.TestCase):
    def test_private_pipe_roundtrip_and_no_secret_in_argv_or_environment(self):
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / "dedicated-helper"
            executable.write_text("synthetic helper fixture; never executed")
            executable.chmod(0o700)
            state = State("synthetic-client", TOKEN, REFRESH, expires_at=1000)
            store = KeychainStore(executable)
            replies = [subprocess.CompletedProcess([], 0, b"", b""),
                       subprocess.CompletedProcess([], 0, state.to_json().encode(), b"")]
            with patch("ticktick_mcp_store.subprocess.run", side_effect=replies) as run:
                with store.transaction():
                    store.save(state)
                    self.assertEqual(store.load(), state)
            self.assertEqual(run.call_args_list[0].args[0], [str(executable), "save"])
            self.assertIn(REFRESH.encode(), run.call_args_list[0].kwargs["input"])
            for call in run.call_args_list:
                self.assertTrue(call.kwargs["capture_output"])
                self.assertNotIn(TOKEN, repr(call.args))
                self.assertNotIn(REFRESH, repr(call.kwargs["env"]))
            self.assertEqual(os.stat(executable.with_suffix(".lock")).st_mode & 0o777, 0o600)

    def test_unprotected_or_symlinked_helper_never_reads_any_state(self):
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / "helper"
            executable.write_text("fixture")
            executable.chmod(0o666)
            link = Path(root) / "link"
            link.symlink_to(executable)
            for candidate in (executable, link, Path("relative")):
                with self.subTest(candidate=candidate), patch("ticktick_mcp_store.subprocess.run") as run, self.assertRaises(AuthError):
                    KeychainStore(candidate).load()
                run.assert_not_called()

    def test_missing_or_failed_keychain_is_redacted_and_not_an_authentication_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / "helper"
            executable.write_text("fixture")
            executable.chmod(0o700)
            for code, error_type in ((2, MissingState), (1, AuthError)):
                reply = subprocess.CompletedProcess([], code, TOKEN.encode(), REFRESH.encode())
                with patch("ticktick_mcp_store.subprocess.run", return_value=reply), self.assertRaises(error_type) as error:
                    KeychainStore(executable).load()
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertNotIn(REFRESH, str(error.exception))

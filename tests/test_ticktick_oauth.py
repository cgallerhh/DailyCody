import base64
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("ticktick_oauth", ROOT / "scripts/setup_ticktick_oauth.py")
oauth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oauth)
import ticktick_tasks

TOKEN = "synthetic-private-access-token"
CODE = "synthetic-private-oauth-code"
CLIENT_SECRET = "synthetic-private-client-secret"
STATE = "synthetic-csrf-state"


class OAuthHandoffTest(unittest.TestCase):
    def test_authorization_has_only_read_scope_and_exact_loopback_redirect(self):
        url = urllib.parse.urlsplit(oauth.authorization_url("synthetic-id", STATE))
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.netloc, "ticktick.com")
        params = urllib.parse.parse_qs(url.query)
        self.assertEqual(params["scope"], ["tasks:read"])
        self.assertEqual(params["redirect_uri"], ["http://127.0.0.1:8765/callback"])
        self.assertEqual(params["state"], [STATE])
        self.assertNotIn(CLIENT_SECRET, oauth.authorization_url("synthetic-id", STATE))

    def test_callback_checks_host_path_single_code_and_state(self):
        valid = "/callback?" + urllib.parse.urlencode({"state": STATE, "code": CODE})
        self.assertEqual(oauth.callback_result(valid, oauth.CALLBACK_HOST, STATE), CODE)
        for path, host in [(valid, "other.invalid"), (valid.replace("callback", "wrong"), oauth.CALLBACK_HOST),
                           (valid.replace(STATE, "wrong"), oauth.CALLBACK_HOST),
                           (valid + "&code=second", oauth.CALLBACK_HOST),
                           ("/callback?state=" + STATE + "&error=" + TOKEN, oauth.CALLBACK_HOST),
                           ("/callback?state=" + STATE, oauth.CALLBACK_HOST)]:
            with self.subTest(path=path), self.assertRaises(oauth.SetupError) as raised:
                oauth.callback_result(path, host, STATE)
            self.assertNotIn(TOKEN, str(raised.exception))
            self.assertNotIn(CODE, str(raised.exception))

    def test_token_exchange_uses_fixed_tls_destination_no_redirect_and_form_body(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"access_token": TOKEN, "scope": "tasks:read"}).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(oauth.urllib.request, "build_opener", return_value=opener) as build:
            result = oauth.exchange_code("synthetic-id", CLIENT_SECRET, CODE)
        self.assertEqual(result["access_token"], TOKEN)
        self.assertIsInstance(build.call_args.args[0], ticktick_tasks.NoRedirect)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://ticktick.com/oauth/token")
        self.assertEqual(req.get_method(), "POST")
        form = urllib.parse.parse_qs(req.data.decode())
        self.assertEqual(form["scope"], ["tasks:read"])
        self.assertEqual(form["grant_type"], ["authorization_code"])
        self.assertEqual(form["code"], [CODE])
        self.assertEqual(form["redirect_uri"], [oauth.REDIRECT_URI])
        self.assertNotIn("client_secret", form)
        auth = req.get_header("Authorization")
        self.assertEqual(base64.b64decode(auth.split()[1]).decode(), "synthetic-id:" + CLIENT_SECRET)

    def test_exchange_errors_never_show_private_response_or_exception(self):
        opener = Mock()
        opener.open.side_effect = RuntimeError(TOKEN + CLIENT_SECRET + CODE)
        with patch.object(oauth.urllib.request, "build_opener", return_value=opener), self.assertRaises(oauth.SetupError) as raised:
            oauth.exchange_code("id", CLIENT_SECRET, CODE)
        for secret in (TOKEN, CLIENT_SECRET, CODE):
            self.assertNotIn(secret, str(raised.exception))

    def test_broader_changed_or_invalid_scope_rejected_before_storage(self):
        for scope in ["tasks:write", "tasks:read tasks:write", "", None, ["tasks:read"], "user:read"]:
            with self.subTest(scope=scope), self.assertRaises(oauth.SetupError):
                oauth.validate_token_response({"access_token": TOKEN, "scope": scope})
        self.assertEqual(oauth.validate_token_response({"access_token": TOKEN, "scope": "tasks:read"}), (TOKEN, None))
        # RFC 6749: an omitted scope means the requested scope was granted.
        self.assertEqual(oauth.validate_token_response({"access_token": TOKEN}), (TOKEN, None))

    def test_expiry_metadata_and_short_invalid_lifetimes(self):
        token, stamp = oauth.validate_token_response({"access_token": TOKEN, "expires_in": 864000})
        self.assertEqual(token, TOKEN)
        self.assertGreater(dt.datetime.fromisoformat(stamp), dt.datetime.now(dt.UTC))
        for ttl in [60, 3600, 0, -1, float("nan"), True, "bad"]:
            with self.subTest(ttl=ttl), self.assertRaises(oauth.SetupError):
                oauth.validate_token_response({"access_token": TOKEN, "expires_in": ttl})

    def test_token_goes_only_to_stdin_of_explicit_actions_secret_destination(self):
        with patch.object(oauth.shutil, "which", return_value="/trusted/gh"), patch.object(oauth.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            oauth.publish_secret(TOKEN)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["/trusted/gh", "secret", "set", "TICKTICK_ACCESS_TOKEN", "--repo", "github.com/cgallerhh/DailyCody", "--app", "actions"])
        self.assertNotIn(TOKEN, repr(argv))
        self.assertEqual(run.call_args.kwargs["input"], TOKEN)
        self.assertNotIn(TOKEN, repr(run.call_args.kwargs["env"]))
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_debug_environment_and_unrelated_credentials_do_not_reach_gh(self):
        with patch.dict(os.environ, {"GH_DEBUG": "api", "GIT_TRACE_CURL": "1", "UNRELATED_SECRET": TOKEN, "GH_HOST": "evil.invalid"}):
            env = oauth.gh_environment()
        self.assertNotIn("GH_DEBUG", env)
        self.assertNotIn("GIT_TRACE_CURL", env)
        self.assertNotIn("UNRELATED_SECRET", env)
        self.assertEqual(env["GH_HOST"], "github.com")

    def test_destination_checks_repository_and_admin_permission(self):
        for data in [{"full_name": "other/repo", "permissions": {"admin": True}},
                     {"full_name": "cgallerhh/DailyCody", "permissions": {"admin": False}}]:
            with self.subTest(data=data), patch.object(oauth, "gh_command", return_value=Mock(stdout=json.dumps(data))), self.assertRaises(oauth.SetupError):
                oauth.check_github_destination()

    def test_receipt_contains_no_credential_or_task_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(oauth, "RESULT_PATH", Path(tmp) / "receipt.json"):
            result = ticktick_tasks.TaskRead(tasks=[{"title": "SYNTHETIC PRIVATE TASK", "notes": TOKEN}], project_count=4, open_task_count=12)
            oauth.save_receipt(result, None)
            receipt = (Path(tmp) / "receipt.json").read_text()
        data = json.loads(receipt)
        self.assertTrue(data["local_probe_including_inbox"])
        self.assertEqual(data["open_task_count"], 12)
        for secret in [TOKEN, CODE, CLIENT_SECRET, "SYNTHETIC PRIVATE TASK"]:
            self.assertNotIn(secret, receipt)

    def test_main_refuses_redirected_input_before_getpass_or_any_mutation(self):
        with patch.object(sys.stdin, "isatty", return_value=False), patch.object(oauth.getpass, "getpass") as password, patch.object(oauth, "publish_secret") as publish, self.assertRaises(oauth.SetupError):
            oauth.main()
        password.assert_not_called()
        publish.assert_not_called()

    def run_main(self, token_response, probe, receipt_error=None):
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(oauth.resource, "setrlimit"), patch.object(oauth, "check_github_destination"), \
             patch.object(oauth.getpass, "getpass", side_effect=["synthetic-id", CLIENT_SECRET]), \
             patch.object(oauth, "receive_code", return_value=CODE), patch.object(oauth, "exchange_code", return_value=token_response), \
             patch.object(ticktick_tasks, "read_tasks", return_value=probe), patch.object(oauth, "publish_secret") as publish, patch.object(oauth, "save_receipt", side_effect=receipt_error) as save, \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            stdout.isatty = lambda: True
            try:
                rc = oauth.main()
            except oauth.SetupError as exc:
                stdout.write(str(exc))
                rc = 1
        return rc, publish, save, stdout.getvalue()

    def test_failed_inbox_or_changed_scope_cannot_install_secret(self):
        for response, probe in [({"access_token": TOKEN, "scope": "tasks:read tasks:write"}, ticktick_tasks.TaskRead()),
                                ({"access_token": TOKEN, "scope": "tasks:read"}, ticktick_tasks.TaskRead(warning="Inbox nicht verfügbar"))]:
            rc, publish, save, output = self.run_main(response, probe)
            self.assertEqual(rc, 1)
            publish.assert_not_called()
            save.assert_not_called()
            self.assertNotIn(TOKEN, output)

    def test_success_installs_only_after_probe_and_never_prints_secret(self):
        result = ticktick_tasks.TaskRead(project_count=4, open_task_count=12, fetched_at="2026-10-06T06:00:00+02:00")
        rc, publish, save, output = self.run_main({"access_token": TOKEN, "scope": "tasks:read"}, result)
        self.assertEqual(rc, 0)
        publish.assert_called_once_with(TOKEN)
        save.assert_called_once()
        self.assertIn("4 Listen einschließlich Inbox", output)
        for secret in [TOKEN, CODE, CLIENT_SECRET]:
            self.assertNotIn(secret, output)

    def test_receipt_failure_reports_saved_secret_without_exposing_error(self):
        rc, publish, save, output = self.run_main(
            {"access_token": TOKEN, "scope": "tasks:read"}, ticktick_tasks.TaskRead(),
            OSError(TOKEN + CLIENT_SECRET))
        self.assertEqual(rc, 1)
        publish.assert_called_once_with(TOKEN)
        self.assertIn("GitHub-Secret wurde gespeichert", output)
        self.assertIn("nicht erneut autorisieren", output)
        for secret in [TOKEN, CODE, CLIENT_SECRET]:
            self.assertNotIn(secret, output)


if __name__ == "__main__":
    unittest.main()

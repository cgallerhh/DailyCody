"""Synthetic handoff tests; no browser, Keychain, network or GitHub calls."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_ticktick_mcp as setup
import ticktick_mcp_auth as auth

TOKEN = "synthetic-private-cody-access"
REFRESH = "synthetic-private-cody-refresh"
PROBE = "synthetic-undated-inbox-id"


def state():
    return auth.State("synthetic-own-client", TOKEN, REFRESH, expires_at=1000)


class HandoffTest(unittest.TestCase):
    def test_default_barrier_blocks_every_setup_action_even_with_environment(self):
        self.assertFalse(setup.HANDOFF_APPROVED)
        with patch.dict(setup.os.environ, {"HANDOFF_APPROVED": "true", "TICKTICK_MCP_INBOX_PROBE_ID": PROBE}), \
                patch.object(setup, "KeychainStore") as keychain, \
                patch.object(setup, "register") as register, \
                patch.object(setup, "consent") as consent, \
                patch.object(setup, "check_github_destination") as github, \
                patch.object(setup, "publish") as publish, self.assertRaises(auth.AuthError):
            setup.main()
        for action in (keychain, register, consent, github, publish):
            action.assert_not_called()

    def test_missing_known_undated_inbox_id_stops_before_new_access_setup(self):
        with patch.object(setup, "HANDOFF_APPROVED", True), \
                patch.dict(setup.os.environ, {}, clear=True), \
                patch.object(setup.sys, "stdin", Mock(isatty=Mock(return_value=True))), \
                patch.object(setup.sys, "stdout", Mock(isatty=Mock(return_value=True))), \
                patch.object(setup, "KeychainStore") as keychain, \
                patch.object(setup, "check_github_destination") as github, \
                self.assertRaises(auth.AuthError):
            setup.main()
        keychain.assert_not_called()
        github.assert_not_called()

    def test_registration_requires_confirmed_public_read_only_refresh_before_login(self):
        accepted = {"client_id": "synthetic-own-client", "token_endpoint_auth_method": "none",
                    "redirect_uris": [auth.REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
                    "scope": "tasks:read"}
        variants = [accepted, {**accepted, "grant_types": ["authorization_code"]},
                    {**accepted, "scope": "tasks:read tasks:write"},
                    {**accepted, "client_secret": "synthetic-secret"},
                    {**accepted, "redirect_uris": ["https://evil.invalid/callback"]}]
        for index, reply in enumerate(variants):
            with self.subTest(index=index), patch.object(setup, "public_json", side_effect=[{}, {}, reply]) as fetch, \
                    patch.object(auth, "validate_discovery"), patch.object(setup, "consent") as consent:
                if index == 0:
                    self.assertEqual(setup.register(), "synthetic-own-client")
                else:
                    with self.assertRaises(auth.AuthError):
                        setup.register()
            consent.assert_not_called()
            self.assertEqual(fetch.call_args.args[1]["scope"], "tasks:read")
            self.assertEqual(fetch.call_args.args[1]["token_endpoint_auth_method"], "none")

    def test_publish_uses_private_stdin_and_access_only_snapshot_for_exact_repository(self):
        with patch.object(setup.time, "time", return_value=100), patch.object(setup, "gh_command") as github:
            setup.publish(state(), PROBE)
        calls = github.call_args_list
        self.assertEqual(len(calls), 2)
        snapshot = auth.State.from_json(calls[0].kwargs["secret_input"])
        self.assertEqual(snapshot.access_token, TOKEN)
        self.assertIsNone(snapshot.refresh_token)
        self.assertNotIn(REFRESH, calls[0].kwargs["secret_input"])
        self.assertEqual(calls[1].kwargs["secret_input"], PROBE)
        for call in calls:
            self.assertEqual(call.args[0][-3:], ["github.com/cgallerhh/DailyCody", "--app", "actions"])
            self.assertNotIn(TOKEN, repr(call.args))
            self.assertNotIn(REFRESH, repr(call.args))
        with patch.object(setup.time, "time", return_value=940), patch.object(setup, "gh_command") as github, \
                self.assertRaises(auth.AuthError):
            setup.publish(state(), PROBE)
        github.assert_not_called()

    def prepared(self, stack, store):
        stack.enter_context(patch.object(setup, "HANDOFF_APPROVED", True))
        stack.enter_context(patch.dict(setup.os.environ, {"TICKTICK_MCP_INBOX_PROBE_ID": PROBE}, clear=True))
        stack.enter_context(patch.object(setup.sys, "stdin", Mock(isatty=Mock(return_value=True))))
        stack.enter_context(patch.object(setup.sys, "stdout", Mock(isatty=Mock(return_value=True))))
        stack.enter_context(patch.object(setup.resource, "setrlimit"))
        stack.enter_context(patch.object(setup, "HELPER", Mock(is_file=Mock(return_value=True))))
        stack.enter_context(patch.object(setup, "check_github_destination"))
        stack.enter_context(patch.object(setup, "KeychainStore", return_value=store))
        store.transaction.return_value = nullcontext()

    def test_failed_probe_keeps_new_approved_seed_without_publishing_or_repeated_login(self):
        store = Mock()
        store.load.side_effect = setup.MissingState("synthetic missing own item")
        with ExitStack() as stack:
            self.prepared(stack, store)
            register = stack.enter_context(patch.object(setup, "register", return_value="synthetic-own-client"))
            consent = stack.enter_context(patch.object(setup, "consent", return_value=state()))
            stack.enter_context(patch.object(setup.harness, "check", side_effect=auth.AuthError("synthetic probe failure")))
            publish = stack.enter_context(patch.object(setup, "publish"))
            with self.assertRaises(auth.AuthError):
                setup.main()
        store.save.assert_called_once_with(state())
        register.assert_called_once()
        consent.assert_called_once()
        publish.assert_not_called()

    def test_existing_own_seed_reused_and_all_three_probes_precede_secret_publish(self):
        store = Mock()
        store.load.return_value = state()
        events = []
        receipts = [{"generation": 0, "email_sent": False}, {"generation": 1, "email_sent": False},
                    {"generation": 1, "email_sent": False}]
        def check(*args, **kwargs):
            events.append(("probe", kwargs))
            return receipts[len(events) - 1]
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            self.prepared(stack, store)
            register = stack.enter_context(patch.object(setup, "register"))
            consent = stack.enter_context(patch.object(setup, "consent"))
            stack.enter_context(patch.object(setup.harness, "check", side_effect=check))
            publish = stack.enter_context(patch.object(setup, "publish", side_effect=lambda *args: events.append(("publish", {}))))
            receipt = Path(root) / "safe-receipt.json"
            stack.enter_context(patch.object(setup, "RECEIPT", receipt))
            self.assertEqual(setup.main(), 0)
            saved = receipt.read_text()
        register.assert_not_called()
        consent.assert_not_called()
        store.save.assert_not_called()
        publish.assert_called_once()
        self.assertEqual(events, [("probe", {}), ("probe", {"force_refresh": True}),
                                  ("probe", {"expected_generation": 1}), ("publish", {})])
        self.assertNotIn(TOKEN, saved)
        self.assertNotIn(REFRESH, saved)
        self.assertNotIn(PROBE, saved)
        self.assertTrue(json.loads(saved)["actions_test_still_required"])

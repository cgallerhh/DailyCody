"""Synthetic cross-thread and settlement regressions, without private messages."""
import base64
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody as c
import commerce_reconciliation as commerce
import waiting_reply as w


def request():
    return {"message_id": "sent-example", "thread_id": "sent-thread", "sort_key": 100000,
            "headers": {"message-id": "<original@example.org>", "subject": "Kündigung", "to": "service@example.org"},
            "subject": "Kündigung", "body": "Bitte bestätigen Sie die Kündigung.", "source_url": "https://mail.google.com/mail/u/0/#all/sent-example"}


def reply():
    return {"message_id": "reply-example", "thread_id": "new-thread", "sort_key": 200000,
            "from": "Service <agent@different.example>", "subject": "Ticket: Bestätigung",
            "headers": {"in-reply-to": "<original@example.org>"},
            "body": "Die Kündigung ist bestätigt. Eine Erstattung wurde veranlasst."}


class WaitingReplyTest(unittest.TestCase):
    def test_exact_reply_reference_allows_changed_thread_sender_and_subject(self):
        self.assertTrue(w.has_reply(request(), [reply()], {"owner@example.org"}, []))

    def test_references_chain_matches_exact_message_id(self):
        item = reply()
        item["headers"] = {"references": "<ancestor@example.org>\n <original@example.org>"}
        self.assertTrue(w.has_reply(request(), [item], set(), []))
        item["headers"] = {"references": "<original@example.org.extra>"}
        self.assertFalse(w.has_reply(request(), [item], set(), []))

    def test_same_sender_or_subject_without_reference_is_not_enough(self):
        item = reply()
        item["headers"] = {}
        item.update(subject="Kündigung", **{"from": "service@example.org"})
        self.assertFalse(w.has_reply(request(), [item], set(), []))

    def test_verified_pair_is_exact_and_never_closes_new_request(self):
        item = reply()
        item["headers"] = {}
        links = [{"request": commerce.fingerprint("message", "sent-example"), "reply": commerce.fingerprint("message", "reply-example")}]
        self.assertTrue(w.has_reply(request(), [item], set(), links))
        self.assertFalse(w.has_reply({**request(), "message_id": "another-request"}, [item], set(), links))
        self.assertFalse(w.has_reply(request(), [{**item, "message_id": "another-reply"}], set(), links))

    def test_acknowledgements_never_answer_even_with_exact_headers(self):
        for changes in (
            {"body": "Wir haben Ihre Nachricht erhalten und melden uns bald."},
            {"subject": "Automatische Antwort", "body": "Vielen Dank."},
            {"headers": {"in-reply-to": "<original@example.org>", "auto-submitted": "auto-replied"}},
            {"body": "Diese Nachricht wurde automatisch erstellt."},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(w.has_reply(request(), [{**reply(), **changes}], set(), []))

    def test_old_and_own_messages_do_not_answer(self):
        self.assertFalse(w.has_reply(request(), [{**reply(), "sort_key": 99999}], set(), []))
        self.assertFalse(w.has_reply(request(), [reply()], {"agent@different.example"}, []))

    def test_receipt_in_same_thread_does_not_answer(self):
        raw = {"internalDate": "200000", "payload": {"headers": [{"name": "From", "value": "service@example.org"}],
               "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Wir haben Ihre Nachricht erhalten.").decode()}}}
        with patch.object(c, "request_json", return_value={"messages": [raw]}):
            self.assertEqual(c.conversation_state("unused", "thread", set(), 100000)["latest_external_at"], 0)

    def test_waiting_intake_searches_whole_request_interval_and_drops_reply(self):
        sent, received = request(), reply()
        for linked in (True, False):
            received["headers"] = {"in-reply-to": "<original@example.org>"} if linked else {}
            with self.subTest(linked=linked), patch.object(c, "search_gmail_refs", side_effect=[[{"id": sent["message_id"]}], [{"id": received["message_id"]}]]) as search, patch.object(
                c, "normalized_gmail_message", side_effect=[sent, received]
            ), patch.object(c, "conversation_state", return_value={"closed": False, "latest_own_at": 100000, "latest_external_at": 0}), patch.object(
                c, "load_completed_delivery_topics", return_value=[]
            ), patch.object(w, "load_links", return_value=[]):
                results = c.list_waiting_for_mail("unused", "owner@example.org", "owner@example.org", "Europe/Berlin")
                self.assertEqual(len(results), 0 if linked else 1)
                self.assertIn("after:99", search.call_args_list[1].args[1])
                if results:
                    self.assertNotIn("headers", results[0])

    def test_reply_does_not_imply_refund_and_user_confirmation_is_scoped(self):
        item = {**reply(), "subject": "Experteer Erstattung", "body": "Erstattung wurde veranlasst."}
        self.assertTrue(w.has_reply(request(), [item], set(), []))
        self.assertEqual(commerce.completion_states(item), set())
        self.assertFalse(commerce.is_completed(item, []))
        event = {"source": "user_confirmation", "scope": "exact_message", "states": ["refunded"],
                 "identities": [commerce.fingerprint("message", "reply-example")], "through_ms": 300000}
        self.assertTrue(commerce.is_completed(item, [event]))
        self.assertFalse(commerce.is_completed({**item, "message_id": "new-refund"}, [event]))
        self.assertFalse(commerce.is_completed({**item, "sort_key": 400000}, [event]))
        self.assertFalse(commerce.is_completed({**item, "body": "Abrechnung prüfen", "subject": "Experteer Abrechnung"}, [event]))
        self.assertFalse(commerce.is_completed(item, [{**event, "source": "mail"}]))


if __name__ == "__main__":
    unittest.main()

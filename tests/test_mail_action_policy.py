"""Anonymized mail-action regressions and controls; no live mailbox data."""

import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody
import mail_policy


NOW = dt.datetime(2026, 10, 1, 8, tzinfo=ZoneInfo("Europe/Berlin"))
RETURN_FOOTER = (
    "Für diesen Artikel gelten unsere Rückgabebedingungen. "
    "Wenn du mit deinem Artikel nicht zufrieden bist, kannst du ihn innerhalb "
    "von 30 Tagen nach Erhalt zurücksenden."
)
MEETING_CONFIRMATION = (
    "Hallo Nora,\n"
    "der Termin am Donnerstag passt. Ich ergänze die Folien vor unserem Gespräch. "
    "Im Termin können wir die offenen Punkte abgleichen: Was ist vorhanden, "
    "was müssten wir aufbauen und was könnten wir über Partner absichern?\n"
    "Viele Grüße\nAlex"
)


class PersonalMailIntakeTest(unittest.TestCase):
    def test_amazon_shipping_notice_is_not_personal_without_bulk_headers(self):
        notice = {
            "from": "Amazon <versandbestaetigung@amazon.de>",
            "subject": "Versendet: Bestellung 305-1111111-2222222",
            "body": f"Hallo Alex, deine Bestellung wurde versendet. {RETURN_FOOTER}",
            "headers": {},
            "labels": ["CATEGORY_UPDATES", "UNREAD"],
            "sort_key": int((NOW - dt.timedelta(days=1)).timestamp() * 1000),
        }
        self.assertFalse(mail_policy.personal_message(notice))
        self.assertFalse(mail_policy.requests_response(notice["subject"], notice["body"]))
        self.assertEqual(daily_cody.list_yesterday_open_mail("unused", NOW, [notice]), [])

    def test_explicit_transactional_mailboxes_are_automated_across_merchants(self):
        for local in (
            "versandbestaetigung", "bestellbestätigung", "order-confirmation",
            "shipping_confirmation", "shipment-tracking+example", "delivery.notification",
        ):
            with self.subTest(local=local):
                self.assertFalse(mail_policy.personal_message({
                    "from": f"Shop <{local}@shop.example>",
                    "headers": {},
                }))

    def test_an_explicit_machine_generated_notice_is_not_personal(self):
        for body in (
            "Diese Nachricht wurde automatisch erstellt. Bitte nicht antworten.",
            "Diese E-Mail ist automatisch generiert.",
            "This message was automatically generated. Please do not reply.",
        ):
            with self.subTest(body=body):
                self.assertFalse(mail_policy.personal_message({
                    "from": "Service <service@shop.example>", "body": body,
                }))

    def test_human_support_at_the_same_merchant_is_retained(self):
        message = {
            "from": "Nora <support@amazon.de>",
            "subject": "Re: Bestellung 305-1111111-2222222",
            "body": "Hallo Alex, ich prüfe die Lieferung. Kannst du mir ein Foto schicken?",
            "labels": ["CATEGORY_UPDATES"],
        }
        self.assertTrue(mail_policy.personal_message(message))
        self.assertTrue(mail_policy.requests_response(message["subject"], message["body"]))

    def test_general_business_and_shipping_inboxes_are_not_blocked(self):
        for local in ("nora", "support", "service", "orders", "shipping"):
            with self.subTest(local=local):
                self.assertTrue(mail_policy.personal_message({
                    "from": f"Nora <{local}@shop.example>",
                    "subject": "Ihre Lieferung",
                    "body": "Ich habe die Bestellung geprüft. Welche Adresse ist richtig?",
                }))

    def test_quoted_machine_notice_does_not_hide_a_human_reply(self):
        body = (
            "Kannst du bitte den fehlenden Artikel prüfen?\n"
            "Am Mittwoch schrieb Service:\n"
            "Diese Nachricht wurde automatisch erstellt."
        )
        self.assertTrue(mail_policy.personal_message({"from": "Nora <nora@example.org>", "body": body}))


class ResponseRequestTest(unittest.TestCase):
    def test_meeting_confirmation_with_own_promise_and_agenda_is_not_waiting(self):
        self.assertFalse(mail_policy.requests_response("Re: Projektgespräch", MEETING_CONFIRMATION))
        self.assertFalse(daily_cody.looks_waiting_for_reply("Re: Projektgespräch", MEETING_CONFIRMATION))

    def test_meeting_regression_does_not_enter_sent_mail_waiting_items(self):
        message = {
            "thread_id": "example-thread",
            "headers": {"to": "Nora <nora@consultancy.example>", "subject": "Re: Projektgespräch"},
            "body": MEETING_CONFIRMATION,
        }
        with patch.object(daily_cody, "search_gmail_refs", return_value=[{"id": "example"}]), patch.object(
            daily_cody, "normalized_gmail_message", return_value=message,
        ), patch.object(daily_cody, "load_completed_delivery_topics", return_value=[]), patch.object(
            daily_cody, "conversation_state",
        ) as state:
            self.assertEqual(daily_cody.list_waiting_for_mail(
                "unused", "Owner <alex+cody@example.org>", "alex@example.org", "Europe/Berlin",
            ), [])
        state.assert_not_called()

    def test_a_real_request_after_the_own_promise_and_agenda_still_counts(self):
        body = MEETING_CONFIRMATION.replace(
            "\nViele Grüße", " Kannst du mir vorab die Teilnehmerliste schicken?\nViele Grüße",
        )
        self.assertTrue(mail_policy.requests_response("Re: Projektgespräch", body))

    def test_a_direct_request_inside_an_agenda_prefixed_sentence_still_counts(self):
        for question in (
            "Kannst du mir vorher die Unterlagen schicken?",
            "Schickst du mir vorher die Unterlagen?",
            "Bis wann schickst du mir die Unterlagen?",
            "Welche Unterlagen brauchen Sie vorab?",
        ):
            with self.subTest(question=question):
                self.assertTrue(mail_policy.requests_response(
                    "Projektgespräch", f"Im Termin besprechen wir die Planung: {question}",
                ))

    def test_a_natural_question_after_an_own_commitment_still_counts(self):
        self.assertTrue(mail_policy.requests_response(
            "Unterlagen", "Ich schicke die Folien morgen. Wann beginnt unser Gespräch?",
        ))

    def test_explicit_agenda_and_self_questions_do_not_require_a_reply(self):
        for body in (
            "Agenda: Was ist vorhanden und was müssten wir aufbauen?",
            "Im Gespräch besprechen wir: Welche Voraussetzungen fehlen?",
            "Im Termin besprechen wir die Planung: Was könnten wir über Partner absichern?",
            "Ich frage mich: Wie würde ich das lösen?",
            "Notiz an mich: Was muss ich noch vorbereiten?",
        ):
            with self.subTest(body=body):
                self.assertFalse(mail_policy.requests_response("Planung", body))

    def test_topic_prefix_alone_does_not_hide_a_real_request(self):
        for body in (
            "Zum Termin: Kannst du Donnerstag bestätigen?",
            "Wie sieht die Agenda aus: erst Technik oder Planung?",
            "Wann können wir im Termin klären: Was fehlt noch?",
        ):
            with self.subTest(body=body):
                self.assertTrue(mail_policy.requests_response("Termin", body))

    def test_reflection_with_a_question_to_the_recipient_stays_actionable(self):
        self.assertTrue(mail_policy.requests_response(
            "Vorschlag", "Ich frage mich, was du davon hältst?",
        ))

    def test_natural_human_questions_are_still_recognized(self):
        for body in (
            "Könnten Sie den Termin bestätigen?",
            "Hast du einen festen Ansprechpartner bei der zuständigen Stelle?",
            "Wann beginnt unser Gespräch?",
            "Passt Donnerstag um 15 Uhr?",
            "Das passt für dich?",
            "Noch Interesse?",
            "Welche Unterlagen fehlen noch?",
            "Darf ich dich morgen anrufen?",
        ):
            with self.subTest(body=body):
                self.assertTrue(mail_policy.requests_response("Rückfrage", body))

    def test_permissions_and_return_conditions_are_not_requests(self):
        for body in (
            RETURN_FOOTER,
            "Bei Fragen kannst du dich jederzeit bei mir melden.",
            "Bei Fragen kannst du mir jederzeit schreiben.",
            "Bei Interesse können Sie uns gerne kontaktieren.",
            "Wenn Sie möchten, können Sie die Unterlagen vorab lesen.",
            "Du kannst die Unterlagen gern verwenden.",
            "Kannst du gerne so machen.",
        ):
            with self.subTest(body=body):
                self.assertFalse(mail_policy.requests_response("Information", body))

    def test_direct_requests_without_a_question_mark_still_count(self):
        for body in (
            "Kannst du mir die Unterlagen schicken",
            "Könnten Sie den Termin bestätigen.",
            "Hallo Nora,\nkannst du mir die Unterlagen schicken.",
            "Bitte gib mir eine kurze Rückmeldung.",
            "Ich bitte um eine Antwort bis morgen.",
            "Lassen Sie mich wissen, ob der Termin passt.",
        ):
            with self.subTest(body=body):
                self.assertTrue(mail_policy.requests_response("Rückfrage", body))

    def test_polite_requests_after_a_condition_or_preamble_still_count(self):
        for body in (
            "Wenn du Zeit hast, kannst du mir bitte die Unterlagen schicken.",
            "Für die Vorbereitung könntest du mir bitte die Teilnehmerliste schicken.",
            "Wenn du Zeit hast, kannst du bitte die Unterlagen schicken.",
            "Für die Vorbereitung könntest du die Teilnehmerliste bitte prüfen.",
            "Wenn du Zeit hast, kannst du mir die Unterlagen schicken.",
        ):
            with self.subTest(body=body):
                self.assertTrue(mail_policy.requests_response("Unterlagen", body))

    def test_a_real_request_is_not_hidden_by_a_permission_elsewhere(self):
        self.assertTrue(mail_policy.requests_response(
            "Unterlagen", "Bei Fragen kannst du dich jederzeit melden. Bitte bestätige den Eingang.",
        ))

    def test_quoted_questions_and_subject_questions_remain_excluded(self):
        for quote in (
            "\nVon: Nora <nora@example.org>\nKannst du bestätigen?",
            "\nOn Wednesday Nora wrote:\nKannst du bestätigen?",
            "\n> Kannst du bestätigen?",
        ):
            with self.subTest(quote=quote):
                self.assertFalse(mail_policy.requests_response("Re: Kannst du bestätigen?", "Der Termin passt." + quote))

    def test_signature_and_url_questions_do_not_create_requests(self):
        self.assertFalse(mail_policy.requests_response(
            "Unterlagen", "Hier ist der Link: https://example.org/?id=123\nViele Grüße\nFragen? Ruf mich an.",
        ))


if __name__ == "__main__":
    unittest.main()

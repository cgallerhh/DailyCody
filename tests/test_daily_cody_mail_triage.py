import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


class MailTriageTest(unittest.TestCase):
    def test_adesso_payroll_reply_is_actionable(self):
        subject = "RE: Gehaltsabrechnungen Christian Galler-114293"
        snippet = (
            "Hallo Christian, wir werden die korrigierten Daten an die Agentur für Arbeit "
            "übermitteln und die fehlenden Unterlagen nachreichen. Hast du einen festen "
            "Ansprechpartner bei der Agentur für Arbeit?"
        )

        self.assertTrue(daily_cody.looks_actionable(subject, snippet))


if __name__ == "__main__":
    unittest.main()

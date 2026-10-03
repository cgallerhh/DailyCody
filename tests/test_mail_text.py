"""Mail HTML parser regressions using synthetic content only."""

import base64
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody
import mail_text


class VisibleMailHTMLTest(unittest.TestCase):
    def test_valueless_class_keeps_visible_text(self):
        self.assertEqual(mail_text.visible_html("<p class>Hallo</p>"), "Hallo")

    def test_valueless_style_keeps_visible_text(self):
        self.assertEqual(mail_text.visible_html("<p style>Hallo</p>"), "Hallo")

    def test_valueless_href_keeps_link_label(self):
        self.assertEqual(mail_text.visible_html("<a href>Hallo</a>"), "Hallo")

    def test_empty_and_missing_attributes_keep_visible_text(self):
        for markup in (
            "<p>Hallo</p>",
            '<p class="" style="">Hallo</p>',
            "<a>Hallo</a>",
            '<a href="">Hallo</a>',
            "<p CLASS STYLE>Hallo</p>",
        ):
            with self.subTest(markup=markup):
                self.assertEqual(mail_text.visible_html(markup), "Hallo")

    def test_hidden_content_stays_hidden_with_valueless_attributes(self):
        for markup in (
            '<div class="gmail_quote" style><p class>Zitat</p></div>',
            '<div style="display: none" class><a href>Versteckt</a></div>',
            '<div style="visibility: hidden" class>Versteckt</div>',
            "<blockquote class style>Zitat</blockquote>",
            "<head class style><title>Versteckt</title></head>",
            "<script class style>Versteckt</script>",
            "<style class>Versteckt</style>",
        ):
            with self.subTest(markup=markup):
                self.assertEqual(
                    mail_text.visible_html(markup + "<p class>Hallo</p>"),
                    "Hallo",
                )

    def test_valid_http_links_are_preserved(self):
        for url in ("https://example.org/details", "http://example.org/details"):
            with self.subTest(url=url):
                self.assertEqual(
                    mail_text.visible_html(f'<a class style href="{url}">Details</a>'),
                    f"Details {url}",
                )

    def test_nested_mime_html_with_valueless_attributes_is_extracted(self):
        markup = (
            "<p class>Hallo</p>"
            "<p style>Aktueller Text</p>"
            "<a href>Details</a>"
            '<div class="gmail_quote" style><p class>Altes Zitat</p></div>'
        )
        html_part = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(markup.encode()).decode()},
        }
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{"mimeType": "multipart/alternative", "parts": [html_part]}],
        }
        for prefer_html in (False, True):
            with self.subTest(prefer_html=prefer_html):
                self.assertEqual(
                    daily_cody.extract_message_text(payload, prefer_html=prefer_html),
                    "Hallo\nAktueller Text\nDetails",
                )


if __name__ == "__main__":
    unittest.main()

"""Extract visible email HTML without CSS, preheaders, or quoted threads."""

from html.parser import HTMLParser
import re


class VisibleMailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        # HTMLParser uses None for attributes written without a value.
        attrs = {name: value or "" for name, value in attrs}
        parent_hidden = self.stack[-1][1] if self.stack else False
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = parent_hidden or tag in {"head", "script", "style", "blockquote"} or "display:none" in style or "visibility:hidden" in style or "gmail_quote" in attrs.get("class", "")
        if tag not in {"br", "img", "meta", "link", "hr", "input", "wbr"}:
            self.stack.append((tag, hidden, attrs.get("href", "")))
        if not hidden and tag in {"br", "p", "div", "tr", "li"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        _, hidden, href = self.stack[index]
        del self.stack[index:]
        if not hidden and tag == "a" and href.startswith(("https://", "http://")):
            self.chunks.append(" " + href + " ")
        if not hidden and tag in {"p", "div", "tr", "li"}:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.chunks.append(data)


def visible_html(value: str) -> str:
    parser = VisibleMailHTML()
    parser.feed(value)
    text = "".join(parser.chunks)
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff\u2007]", "", text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())

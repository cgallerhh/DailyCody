#!/usr/bin/env python3
"""Export the curated BEWERBUNGEN dashboard into a Daily Cody snapshot."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DASHBOARD_PATH = Path(
    "/Users/cgaller/Library/Mobile Documents/com~apple~CloudDocs/"
    "OBSIDIAN/Vaults/LLM-Wiki/BEWERBUNGEN/pages/_core/Bewerbungs-Dashboard.md"
)
DEFAULT_OUTPUT_PATH = ROOT_DIR / "data" / "application_wiki_snapshot.json"


def main() -> int:
    dashboard_path = Path(os.getenv("APPLICATION_WIKI_DASHBOARD_PATH", str(DEFAULT_DASHBOARD_PATH)))
    output_path = Path(os.getenv("APPLICATION_WIKI_SNAPSHOT_PATH", str(DEFAULT_OUTPUT_PATH)))
    if not dashboard_path.exists():
        raise SystemExit(f"Dashboard not found: {dashboard_path}")

    text = dashboard_path.read_text(encoding="utf-8")
    snapshot = build_snapshot(text, dashboard_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Application wiki snapshot written: {output_path}")
    return 0


def build_snapshot(text: str, dashboard_path: Path) -> dict[str, object]:
    waiting_for = extract_waiting_for(text)
    pipeline_items = extract_pipeline_items(text)
    blocked_items = extract_not_active_items(text)
    action_overrides = build_action_overrides(pipeline_items, blocked_items)
    return {
        "source": str(dashboard_path),
        "dashboard_updated": extract_frontmatter_value(text, "updated") or extract_frontmatter_value(text, "last_verified"),
        "waiting_for": waiting_for,
        "items": pipeline_items,
        "action_overrides": action_overrides,
    }


def extract_frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, flags=re.M)
    return match.group(1).strip() if match else ""


def extract_waiting_for(text: str) -> list[dict[str, str]]:
    section = extract_section(text, "### Waiting for...")
    output = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        clean = clean_markdown(stripped)
        clean = re.sub(r"^- \[[ xX]\]\s*", "", clean).strip()
        subject, detail = split_subject_detail(clean)
        detail = clean_waiting_detail(detail)
        output.append(
            {
                "subject": subject,
                "detail": detail,
                "next_action": derive_waiting_next_action(detail),
                "since": extract_waiting_since(clean),
            }
        )
    return output


def extract_pipeline_items(text: str) -> list[dict[str, str]]:
    section = extract_section(text, "## Aktive Pipeline")
    rows = parse_markdown_table(section)
    output = []
    for row in rows:
        if len(row) < 4:
            continue
        output.append(
            {
                "priority": clean_markdown(row[0]),
                "company": clean_markdown(row[1]),
                "status": clean_markdown(row[2]),
                "next_action": clean_markdown(row[3]),
            }
        )
    return output


def extract_not_active_items(text: str) -> list[dict[str, str]]:
    section = extract_section(text, "## Nicht Aktiv Ansprechen")
    rows = parse_markdown_table(section)
    output = []
    for row in rows:
        if len(row) < 2:
            continue
        output.append({"company": clean_markdown(row[0]), "reason": clean_markdown(row[1])})
    return output


def build_action_overrides(
    pipeline_items: list[dict[str, str]], blocked_items: list[dict[str, str]]
) -> list[dict[str, object]]:
    overrides: dict[str, dict[str, object]] = {}
    for item in blocked_items:
        topic = item["company"]
        overrides[normalize_key(topic)] = {
            "topic": topic,
            "action": "do_not_follow_up",
            "reason": item["reason"],
            "aliases": aliases_for_topic(topic),
        }

    wait_only_markers = (
        "keine weitere aktive nachfrage",
        "nicht weiter nachfassen",
        "nicht eskalieren",
        "keine informelle",
        "keine linkedin",
        "keine aktive nachfassaktion",
    )
    closed_markers = (
        "geschlossen",
        "beendet",
        "zurueckgestellt",
        "zurückgestellt",
        "pausiert",
        "nicht aktiv",
        "keine beruecksichtigung",
        "keine berücksichtigung",
    )
    for item in pipeline_items:
        topic = item["company"]
        haystack = normalize_key(" ".join(item.values()))
        if any(marker in haystack for marker in closed_markers):
            action = "closed" if any(marker in haystack for marker in ("geschlossen", "beendet")) else "paused"
        elif any(marker in haystack for marker in wait_only_markers):
            action = "wait_only"
        else:
            continue
        overrides.setdefault(
            normalize_key(topic),
            {
                "topic": topic,
                "action": action,
                "reason": item["next_action"] or item["status"],
                "aliases": aliases_for_topic(topic),
            },
        )
    return list(overrides.values())


def extract_section(text: str, heading_prefix: str) -> str:
    lines = text.splitlines()
    start = None
    level = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(heading_prefix):
            start = index + 1
            level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start is None or level is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                end = index
                break
    return "\n".join(lines[start:end])


def parse_markdown_table(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and not rows:
            rows.append(cells)
            continue
        if cells:
            rows.append(cells)
    return rows[1:] if rows else []


def clean_markdown(value: str) -> str:
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", lambda match: match.group(1).split("/")[-1], value)
    value = re.sub(r"\s+", " ", value)
    return prettify_german(value.strip())


def clean_waiting_detail(value: str) -> str:
    value = re.sub(r"\s+Quelle:\s+.*$", "", value).strip()
    return prettify_german(value)


def split_subject_detail(value: str) -> tuple[str, str]:
    if ":" not in value:
        return strip_long(value, 120), ""
    subject, detail = value.split(":", 1)
    return strip_long(subject.strip(), 120), strip_long(detail.strip(), 320)


def derive_waiting_next_action(detail: str) -> str:
    lowered = detail.lower()
    if "nicht weiter nachfassen" in lowered or "keine weitere aktive nachfrage" in lowered:
        return "Abwarten; nicht aktiv nachfassen."
    if "abwarten" in lowered:
        return "Abwarten."
    if "nachfrage" in lowered or "nachfassen" in lowered:
        return "Nachfrage erst im genannten Fenster prüfen."
    return ""


def extract_waiting_since(value: str) -> str:
    match = re.search(r"\b(?:vom|am)\s+(\d{2}\.\d{2}\.\d{4})\b", value, flags=re.I)
    return match.group(1) if match else ""


def prettify_german(value: str) -> str:
    replacements = {
        "Rueckmeldung": "Rückmeldung",
        "rueckmeldung": "Rückmeldung",
        "zurueckgestellt": "zurückgestellt",
        "Zurueckgestellt": "Zurückgestellt",
        "bestaetigt": "bestätigt",
        "Bestaetigt": "Bestätigt",
        "Bestaetigung": "Bestätigung",
        "bestaetigung": "Bestätigung",
        "angekuendigt": "angekündigt",
        "Angekuendigt": "Angekündigt",
        "kuendigt": "kündigt",
        "Kuendigt": "Kündigt",
        "spaetestens": "spätestens",
        "Spaetestens": "Spätestens",
        "pruefen": "prüfen",
        "Pruefen": "Prüfen",
        "fuer": "für",
        "Fuer": "Für",
        "zustaendige": "zuständige",
        "Zustaendige": "Zuständige",
        "Empfaenger": "Empfänger",
        "Naechste": "Nächste",
        "Beruecksichtigung": "Berücksichtigung",
        "beruecksichtigung": "Berücksichtigung",
        "Geprueft": "Geprüft",
        "geprueft": "geprüft",
        "Vorlaeufig": "Vorläufig",
        "vorlaeufig": "vorläufig",
        "Fuechtenhans": "Füchtenhans",
        "ueber": "über",
        "Ueber": "Über",
        "laeuft": "läuft",
        "Joerg-Schreiber": "Jörg Schreiber",
        "Joerg Schreiber": "Jörg Schreiber",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def aliases_for_topic(topic: str) -> list[str]:
    aliases = {topic}
    lowered = topic.lower()
    if "faktor" in lowered or "tention" in lowered:
        aliases.update(
            {
                "Faktor-D",
                "Faktor D",
                "XD-Faktor",
                "xD Consulting",
                "x-tention",
                "xtention",
                "x tension",
                "Caroline Engljaehringer",
                "Caroline Engljähringer",
                "Frau Engljaehringer",
                "Frau Engljähringer",
            }
        )
    if "hcvision" in lowered or "hcvision" in normalize_key(topic):
        aliases.update({"hcVISION", "hc:VISION", "hc vision", "Jörg Schreiber", "Joerg Schreiber", "Volker Keim"})
    if "dvelop" in lowered:
        aliases.update({"d.velop", "dvelop"})
    return sorted(alias for alias in aliases if alias)


def normalize_key(value: str) -> str:
    value = value.lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_long(value: str, max_len: int) -> str:
    return value[: max_len - 1] + "…" if len(value) > max_len else value


if __name__ == "__main__":
    raise SystemExit(main())

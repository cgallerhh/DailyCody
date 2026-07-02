"""Reusable delivery detection for Gmail-like message exports.

The module is intentionally transport-agnostic: callers pass normalized message
dicts, and this code classifies, groups, suppresses, and summarizes deliveries.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import re
import unicodedata
from typing import Any


DELIVERY_LOOKBACK_DAYS = 60
DELIVERY_SEARCH_MAX_RESULTS_PER_QUERY = 80
DELIVERY_SEARCH_TOTAL_LIMIT = 360


def detect_open_deliveries(
    messages: list[dict[str, Any]],
    now: dt.datetime,
    *,
    completed_topics: list[str] | None = None,
    own_addresses: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return open delivery items from normalized message dictionaries.

    Expected message keys are flexible: `from`, `subject`, `snippet`, `body` or
    `text`, `date`, `thread_id`, and `sort_key`/`internal_date` when available.
    """

    candidates = []
    for message in messages:
        sender = message_sender(message)
        if own_addresses and extract_email_addresses(sender).intersection(own_addresses):
            continue
        candidate = delivery_candidate_from_message(message, now, completed_topics=completed_topics)
        if candidate:
            candidates.append(candidate)
    return summarize_delivery_candidates(candidates, now)


def delivery_candidate_from_message(
    message: dict[str, Any],
    now: dt.datetime,
    *,
    completed_topics: list[str] | None = None,
) -> dict[str, Any] | None:
    subject = str(message.get("subject") or "(ohne Betreff)")
    sender = message_sender(message)
    snippet = str(message.get("snippet") or "")
    text = str(message.get("body") or message.get("text") or "")
    if is_delivery_noise(subject, snippet, text):
        return None
    if not looks_like_delivery(subject, snippet, text):
        return None
    status = classify_delivery_status(subject, snippet, text)
    if status == "unknown":
        return None
    display_title = delivery_display_title(subject, sender, text)
    if is_suppressed_topic(subject, snippet, text, display_title, completed_topics=completed_topics):
        return None
    links = extract_tracking_links(text)
    return {
        "from": sender,
        "subject": display_title,
        "date": str(message.get("date") or ""),
        "snippet": delivery_status_summary(status, subject, snippet, text),
        "status": status,
        "status_rank": delivery_status_rank(status),
        "topic_key": normalize_delivery_key(subject, sender, text),
        "thread_id": str(message.get("thread_id") or message.get("threadId") or ""),
        "sort_key": message_sort_key(message),
        "eta_end_date": extract_delivery_eta_end_date(now, subject, snippet, text),
        "tracking_links": links[:3],
        "details": strip_long(clean_mail_excerpt(text), 900),
    }


def message_sender(message: dict[str, Any]) -> str:
    return str(message.get("from") or message.get("from_") or message.get("sender") or "")


def message_sort_key(message: dict[str, Any]) -> int:
    for key in ("sort_key", "internal_date", "internalDate"):
        value = message.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    timestamp = message.get("email_ts") or message.get("timestamp")
    if isinstance(timestamp, str):
        parsed = parse_message_datetime(timestamp)
        if parsed:
            return int(parsed.timestamp() * 1000)
    return 0


def parse_message_datetime(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def delivery_search_queries() -> list[str]:
    base = f"newer_than:{DELIVERY_LOOKBACK_DAYS}d in:anywhere -in:trash -in:spam"
    status_terms = (
        "{bestellung bestellt bestellnummer versendet versandt verschickt versandbereit lieferung zustellung "
        "zugestellt geliefert sendung paket tracking sendungsstatus sendungsnummer \"kommt heute\" "
        "\"in zustellung\" \"auf dem weg\" \"ist unterwegs\" \"liegt nebenan\" shipped delivered dispatched arriving}"
    )
    simple_queries = [
        # Run exact merchant/carrier lookups first. They are cheap and prevent
        # broad status queries from filling the total cap before DHL/Amazon/etc.
        f"{base} -category:promotions from:dhl.de",
        f"{base} -category:promotions from:amazon.de",
        f"{base} -category:promotions from:amazon.com",
        f"{base} -category:promotions from:service.bestsecret.com",
        f"{base} -category:promotions from:partner-program@bestsecret.com",
        f"{base} -category:promotions from:golighter.de",
        f"{base} -category:promotions from:wellstermedical.com",
        f"{base} -category:promotions from:myhermes.de",
        f"{base} -category:promotions from:hermesworld.com",
        f"{base} -category:promotions from:dpd.de",
        f"{base} -category:promotions from:ups.com",
        f"{base} -category:promotions from:gls-germany.com",
        f"{base} -category:promotions sendungsnummer",
        f"{base} -category:promotions bestsecret",
    ]
    structured_queries = [
        f"{base} -category:promotions {status_terms}",
        (
            f"{base} {{label:Amazon from:amazon.de from:amazon.com amazon}} "
            "{bestellung bestellt bestellnr bestellnummer versendet versandt geliefert zugestellt lieferung "
            "zustellung tracking paket shipped delivered arriving}"
        ),
        (
            f"{base} {{from:service.bestsecret.com from:partner-program@bestsecret.com bestsecret BESTSECRET}} "
            "{bestellung bestellnummer versandbereit versendet versand sendung lieferung zustellung tracking paket "
            "dhl hermes}"
        ),
        (
            f"{base} {{from:golighter.de from:wellstermedical.com golighter GoLighter wellster Wellster}} "
            "{medikament apotheke rezept bestellnummer sendung sendungsnummer lieferstatus dhl lieferung "
            "versandvorbereitung \"auf dem weg\" \"kommt heute\" \"liegt nebenan\"}"
        ),
        (
            f"{base} {{from:dhl.de from:myhermes.de from:hermesworld.com from:dpd.de from:ups.com "
            "from:gls-germany.com DHL Hermes DPD UPS GLS} "
            "{sendung paket zustellung zugestellt geliefert unterwegs \"kommt heute\" \"liegt nebenan\" "
            "sendungsstatus sendungsnummer}"
        ),
        # Fallback queries deliberately avoid grouped Gmail syntax. GitHub
        # Actions talks to the raw Gmail API; these keep merchant/carrier
        # coverage even if a complex query is interpreted differently.
        f"{base} -category:promotions from:dhl.de",
        f"{base} -category:promotions from:myhermes.de",
        f"{base} -category:promotions from:hermesworld.com",
        f"{base} -category:promotions from:dpd.de",
        f"{base} -category:promotions from:ups.com",
        f"{base} -category:promotions from:gls-germany.com",
        f"{base} -category:promotions from:amazon.de",
        f"{base} -category:promotions from:amazon.com",
        f"{base} -category:promotions from:service.bestsecret.com",
        f"{base} -category:promotions from:partner-program@bestsecret.com",
        f"{base} -category:promotions bestsecret",
        f"{base} -category:promotions from:golighter.de",
        f"{base} -category:promotions from:wellstermedical.com",
        f"{base} -category:promotions sendungsnummer",
    ]
    broad_safety_net = [
        "newer_than:14d in:anywhere -in:trash -in:spam -from:me -category:promotions -category:social",
        "newer_than:30d in:anywhere -in:trash -in:spam category:updates",
    ]
    return simple_queries + structured_queries + broad_safety_net


def is_own_delivery_sender(sender: str, sender_email: str, recipient_email: str) -> bool:
    own_addresses = delivery_completion_addresses(sender_email, recipient_email)
    sender_addresses = extract_email_addresses(sender)
    return bool(own_addresses and sender_addresses and own_addresses.intersection(sender_addresses))


def extract_tracking_links(text: str) -> list[str]:
    links = re.findall(r"https?://[^\s<>\")]+", text)
    wanted = []
    keywords = (
        "track",
        "tracking",
        "sendung",
        "sendungsverfolgung",
        "liefer",
        "ship",
        "dhl",
        "hermes",
        "amazon",
        "dpd",
        "ups",
        "gls",
        "post",
    )
    for link in links:
        clean = link.rstrip(".,;:")
        if (
            any(keyword in clean.lower() for keyword in keywords)
            or has_tracking_context_near_link(text, clean)
        ) and clean not in wanted:
            wanted.append(clean)
    return wanted


def has_tracking_context_near_link(text: str, link: str) -> bool:
    index = text.find(link)
    if index == -1:
        return False
    context = normalize_status_text(text[max(0, index - 220) : index + len(link) + 80])
    markers = (
        "lieferung verfolgen",
        "sendung verfolgen",
        "sendungsverfolgung",
        "paket verfolgen",
        "dein paket wurde versendet",
        "ankunft morgen",
        "versendet",
    )
    return any(marker in context for marker in markers)


def looks_like_delivery(subject: str, snippet: str, text: str) -> bool:
    if classify_delivery_status_from_subject(subject):
        return True
    haystack = f"{subject} {snippet} {text[:1200]}".lower()
    normalized = normalize_status_text(haystack)
    strong_markers = (
        "bestellung",
        "bestellt",
        "lieferung",
        "zugestellt",
        "zustellung",
        "sendung",
        "tracking",
        "sendungsverfolgung",
        "sendungsnummer",
        "sendungsstatus",
        "paket",
        "medikament",
        "apotheke",
        "versandbereit",
        "versandvorbereitung",
        "unterwegs",
        "auf dem weg",
        "dhl",
        "hermes",
        "dpd",
        "ups",
        "gls",
        "amazon",
        "proraso",
        "bestsecret",
        "best secret",
        "golighter",
        "wellster",
        "comic",
    )
    if any(marker in haystack for marker in strong_markers):
        return True
    weak_status_markers = (
        "versandt",
        "versendet",
        "verschickt",
        "unterwegs",
        "auf dem weg",
        "kommt heute",
        "shipped",
        "dispatched",
    )
    known_sender_markers = (
        "amazon",
        "bestsecret",
        "best secret",
        "dhl",
        "dpd",
        "gls",
        "golighter",
        "hermes",
        "ups",
        "wellster",
    )
    return any(marker in normalized for marker in weak_status_markers) and any(
        marker in normalized for marker in known_sender_markers
    )


def is_delivery_noise(subject: str, snippet: str, text: str) -> bool:
    haystack = normalize_status_text(f"{subject} {snippet} {text[:800]}")
    noise_markers = (
        "kurzbefragung",
        "umfrage",
        "verlosung",
        "kundenbefragung",
        "bewerten sie",
        "ihre meinung",
    )
    if any(marker in haystack for marker in noise_markers):
        return True
    if is_return_or_info_delivery_noise(subject, snippet, haystack):
        return True
    if is_non_delivery_account_notification(haystack):
        return True
    if "hvv" in haystack and any(marker in haystack for marker in ("ticket", "fahrkarte", "onlineshop")):
        return True
    return False


def is_return_or_info_delivery_noise(subject: str, snippet: str, haystack: str) -> bool:
    short_haystack = normalize_status_text(f"{subject} {snippet}")
    if any(marker in short_haystack for marker in ("retoure", "rucksendung", "rücksendung")):
        return True
    golighter_info_markers = (
        "die nächsten lieferungen kommen automatisch",
        "die nachsten lieferungen kommen automatisch",
        "behandlungs check",
        "pflicht check up",
        "fragebogen ausfüllen",
        "fragebogen ausfullen",
        "behandlungsplan wird fortgesetzt",
        "kurz erklärt",
        "kurz erklart",
    )
    current_delivery_markers = (
        "rezept wurde ausgestellt",
        "sendungsnummer",
        "ist auf dem weg",
        "apotheke hat dein medikament an dhl übergeben",
        "apotheke hat dein medikament an dhl ubergeben",
    )
    return any(marker in haystack for marker in golighter_info_markers) and not any(
        marker in haystack for marker in current_delivery_markers
    )


def is_non_delivery_account_notification(haystack: str) -> bool:
    official_markers = (
        "bundesagentur fur arbeit",
        "bundesagentur für arbeit",
        "arbeitsagentur",
        "jobcenter",
    )
    notification_markers = (
        "neue mitteilung",
        "neue mitteilungen",
        "postfach",
        "bescheid",
        "dokument",
    )
    delivery_markers = (
        "amazon",
        "bestellung",
        "paket",
        "sendung",
        "tracking",
        "dhl",
        "hermes",
        "dpd",
        "ups",
        "gls",
    )
    return (
        any(marker in haystack for marker in official_markers)
        and any(marker in haystack for marker in notification_markers)
        and not any(marker in haystack for marker in delivery_markers)
    )


def classify_delivery_status(subject: str, snippet: str, text: str) -> str:
    subject_status = classify_delivery_status_from_subject(subject)
    if subject_status:
        return subject_status
    haystack = normalize_status_text(f"{subject} {snippet} {text[:1200]}")
    if any(
        marker in haystack
        for marker in (
            "ist angekommen",
            "sendung ist angekommen",
            "geliefert",
            "zugestellt",
            "wurde zugestellt",
            "ist zugestellt",
            "zugestellt am",
            "abgelegt",
            "abgegeben",
            "ablageort",
            "liegt nebenan",
            "delivered",
            "has been delivered",
        )
    ):
        return "delivered"
    if any(
        marker in haystack
        for marker in (
            "in zustellung",
            "kommt heute",
            "sendung kommt heute",
            "wird heute zugestellt",
            "zustellung heute",
            "out for delivery",
            "arriving today",
        )
    ):
        return "out_for_delivery"
    if has_pre_shipment_order_signal(haystack) and not has_actual_shipment_signal(haystack):
        return "ordered"
    if any(
        marker in haystack
        for marker in (
            "versendet",
            "versandt",
            "verschickt",
            "versandbereit",
            "unterwegs",
            "auf dem weg",
            "shipped",
            "dispatched",
            "on the way",
        )
    ):
        return "shipped"
    if any(
        marker in haystack
        for marker in (
            "bestellt",
            "bestellung eingegangen",
            "bestätigung deiner bestellung",
            "bestellbestätigung",
            "vielen dank für ihre bestellung",
            "vielen dank für deine bestellung",
            "rezept wurde ausgestellt",
            "versandvorbereitung",
            "bestellung bestätigt",
            "order placed",
            "order confirmation",
            "order confirmed",
        )
    ):
        return "ordered"
    if "tracking" in haystack or "sendung verfolgen" in haystack:
        return "shipped"
    return "unknown"


def has_pre_shipment_order_signal(haystack: str) -> bool:
    markers = (
        "versandvorbereitung",
        "rezept wurde ausgestellt",
        "bald erhältst du dein medikament",
        "bald erhaltst du dein medikament",
        "wir beginnen nun mit den vorbereitungen",
        "bestellbestätigung",
        "bestellbestatigung",
        "vielen dank für ihre bestellung",
        "vielen dank fur ihre bestellung",
        "vielen dank für deine bestellung",
        "vielen dank fur deine bestellung",
    )
    return any(marker in haystack for marker in markers)


def has_actual_shipment_signal(haystack: str) -> bool:
    markers = (
        "sendungsnummer",
        "lieferung verfolgen",
        "sendung verfolgen",
        "apotheke hat dein medikament an dhl übergeben",
        "apotheke hat dein medikament an dhl ubergeben",
        "dein paket wurde versendet",
        "ihre bestellung ist versandbereit",
        "bestellung ist versandbereit",
        "wurde von uns bearbeitet und wird ihnen voraussichtlich",
        "wurde soeben bearbeitet und wird demnächst",
        "wurde soeben bearbeitet und wird demnachst",
    )
    return any(marker in haystack for marker in markers)


def classify_delivery_status_from_subject(subject: str) -> str:
    normalized = normalize_status_text(subject)
    if normalized.startswith(("geliefert", "zugestellt", "delivered")) or "liegt nebenan" in normalized:
        return "delivered"
    if normalized.startswith(("in zustellung", "out for delivery", "arriving today")) or (
        "sendung" in normalized and "kommt heute" in normalized
    ):
        return "out_for_delivery"
    if normalized.startswith(
        (
            "versendet",
            "versandt",
            "verschickt",
            "shipped",
            "dispatched",
            "dein medikament ist auf dem weg",
        )
    ) or any(marker in normalized for marker in ("sendung ist unterwegs", "sendung ist auf dem weg", "versandbereit")):
        return "shipped"
    if normalized.startswith(
        (
            "bestellt",
            "bestellung",
            "vielen dank für ihre bestellung",
            "vielen dank fur ihre bestellung",
            "dein rezept wurde ausgestellt",
            "order placed",
            "order confirmation",
        )
    ):
        return "ordered"
    return ""


def delivery_status_rank(status: str) -> int:
    return {
        "ordered": 1,
        "shipped": 2,
        "out_for_delivery": 3,
        "delivered": 4,
    }.get(status, 0)


def summarize_delivery_candidates(items: list[dict[str, Any]], now: dt.datetime) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = item.get("topic_key", "")
        if not key:
            continue
        grouped.setdefault(key, []).append(item)

    now_ms = int(now.timestamp() * 1000)
    current = []
    for group in grouped.values():
        latest_delivered = max(
            (item.get("sort_key", 0) for item in group if item.get("status") == "delivered"),
            default=0,
        )
        active_items = [
            item
            for item in group
            if item.get("status") != "delivered"
            and item.get("sort_key", 0) > latest_delivered
            and not is_stale_delivery_item(item, now)
        ]
        if active_items:
            current.append(
                max(
                    active_items,
                    key=lambda item: (item.get("status_rank", 0), item.get("sort_key", 0)),
                )
            )

    current.sort(key=lambda item: item.get("sort_key", 0), reverse=True)
    return [
        {
            key: value
            for key, value in item.items()
            if key not in {"topic_key", "thread_id", "sort_key", "status_rank"}
        }
        for item in current[:8]
    ]


def is_stale_delivery_item(item: dict[str, Any], now: dt.datetime) -> bool:
    now_ms = int(now.timestamp() * 1000)
    age_hours = delivery_age_hours(item, now_ms)
    status = item.get("status")
    eta_end_date = parse_delivery_item_date(item.get("eta_end_date"))
    if eta_end_date and eta_end_date < now.date():
        return True
    if eta_end_date and eta_end_date == now.date() and status in {"shipped", "out_for_delivery"} and now.hour >= 20:
        return True
    if status == "out_for_delivery":
        return age_hours > 36
    if status == "shipped":
        return age_hours > 14 * 24
    if status == "ordered":
        return age_hours > 10 * 24
    return False


def delivery_age_hours(item: dict[str, Any], now_ms: int) -> float:
    sort_key = item.get("sort_key", 0)
    if not isinstance(sort_key, int):
        try:
            sort_key = int(sort_key)
        except (TypeError, ValueError):
            return 0
    if sort_key <= 0:
        return 0
    return max(0, now_ms - sort_key) / 3_600_000


def delivery_status_summary(status: str, subject: str, snippet: str, text: str) -> str:
    carrier = extract_delivery_carrier(subject, snippet, text)
    carrier_text = f" per {carrier}" if carrier else ""
    if status == "out_for_delivery":
        summary = f"in Zustellung{carrier_text}"
    elif status == "shipped":
        summary = f"versendet{carrier_text}"
    elif status == "ordered":
        summary = "bestellt"
    else:
        summary = "geliefert"
    eta = extract_delivery_eta(subject, snippet, text)
    return f"{summary}, {eta}" if eta and normalize_status_text(eta) not in normalize_status_text(summary) else summary


def extract_delivery_eta_end_date(now: dt.datetime, *values: str) -> str:
    raw_text = clean_mail_excerpt(" ".join(values))
    date_range = re.search(
        r"zustellung\s*:?\s*\d{1,2}\.\s*([A-Za-zÄÖÜäöü]+)\s*[-–]\s*(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)",
        raw_text,
        flags=re.I,
    )
    if date_range:
        parsed = parse_german_month_date(int(date_range.group(2)), date_range.group(3), now)
        return parsed.isoformat() if parsed else ""

    single_date = re.search(
        r"(?:zustellung|ankunft|lieferung)\s*(?:bis|am|:)?\s*(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)",
        raw_text,
        flags=re.I,
    )
    if single_date:
        parsed = parse_german_month_date(int(single_date.group(1)), single_date.group(2), now)
        return parsed.isoformat() if parsed else ""
    numeric_date = extract_numeric_delivery_date(now, raw_text)
    if numeric_date:
        return numeric_date.isoformat()
    return ""


def extract_numeric_delivery_date(now: dt.datetime, raw_text: str) -> dt.date | None:
    for pattern in numeric_delivery_date_patterns():
        for match in re.finditer(pattern, raw_text, flags=re.I):
            parsed = parse_numeric_delivery_date(match.group(1), match.group(2), match.group(3) or "", now)
            if parsed:
                return parsed
    return None


def numeric_delivery_date_patterns() -> tuple[str, ...]:
    return (
        r"(?:voraussichtliche\s+zustellung\s+am|voraussichtlich\s+am|kommt\s+voraussichtlich\s+am|"
        r"wird\s+(?:ihnen|dir)\s+voraussichtlich\s+am|zustellung\s+am)\s+"
        r"(?:[A-Za-zÄÖÜäöü]+,\s*(?:den\s*)?)?(\d{1,2})\.(\d{1,2})\.?(?:\s*(\d{2,4}))?",
        r"am\s+(?:[A-Za-zÄÖÜäöü]+,\s*(?:den\s*)?)?(\d{1,2})\.(\d{1,2})\.?"
        r"(?:\s*(\d{2,4}))?\s+zugestellt",
    )


def parse_numeric_delivery_date(day_text: str, month_text: str, year_text: str, now: dt.datetime) -> dt.date | None:
    try:
        day = int(day_text)
        month = int(month_text)
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
        else:
            year = now.year
        candidate = dt.date(year, month, day)
    except ValueError:
        return None
    if not year_text and candidate < now.date() - dt.timedelta(days=180):
        candidate = dt.date(candidate.year + 1, candidate.month, candidate.day)
    if not year_text and candidate > now.date() + dt.timedelta(days=180):
        candidate = dt.date(candidate.year - 1, candidate.month, candidate.day)
    return candidate


def parse_german_month_date(day: int, month_name: str, now: dt.datetime) -> dt.date | None:
    month = german_month_number(month_name)
    if not month:
        return None
    year = now.year
    candidate = dt.date(year, month, day)
    if candidate < now.date() - dt.timedelta(days=240):
        candidate = dt.date(year + 1, month, day)
    if candidate > now.date() + dt.timedelta(days=240):
        candidate = dt.date(year - 1, month, day)
    return candidate


def german_month_number(value: str) -> int | None:
    normalized = normalize_search_text(value)
    months = {
        "januar": 1,
        "jan": 1,
        "februar": 2,
        "feb": 2,
        "maerz": 3,
        "marz": 3,
        "märz": 3,
        "mrz": 3,
        "april": 4,
        "apr": 4,
        "mai": 5,
        "juni": 6,
        "jun": 6,
        "juli": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "oktober": 10,
        "okt": 10,
        "november": 11,
        "nov": 11,
        "dezember": 12,
        "dez": 12,
    }
    return months.get(normalized)


def parse_delivery_item_date(value: Any) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def extract_delivery_eta(*values: str) -> str:
    raw_text = clean_mail_excerpt(" ".join(values))
    date_range = re.search(
        r"zustellung\s*:\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+)\s*[-–]\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöü]+)",
        raw_text,
        flags=re.I,
    )
    if date_range:
        return f"Zustellung {date_range.group(1)}–{date_range.group(2)}"
    numeric_eta = extract_numeric_delivery_eta(raw_text)
    if numeric_eta:
        return numeric_eta
    relative_eta = re.search(r"zustellung\s*:\s*([A-Za-zÄÖÜäöü]+)(?:\s|$)", raw_text, flags=re.I)
    if relative_eta:
        return f"Zustellung {relative_eta.group(1)}"
    working_days = re.search(
        r"innerhalb\s+von\s+(\d{1,2}\s*[-–]\s*\d{1,2}\s+werktagen?)",
        raw_text,
        flags=re.I,
    )
    if working_days:
        return "in " + re.sub(r"\s+", " ", working_days.group(1)).strip()
    haystack = normalize_status_text(raw_text)
    if "ankunft morgen" in haystack:
        return "Ankunft morgen"
    if any(marker in haystack for marker in ("kommt heute", "ankunft heute", "wird heute zugestellt")):
        return "kommt heute"
    return ""


def extract_numeric_delivery_eta(raw_text: str) -> str:
    for pattern in numeric_delivery_date_patterns():
        match = re.search(pattern, raw_text, flags=re.I)
        if not match:
            continue
        day = int(match.group(1))
        month = int(match.group(2))
        year = match.group(3) or ""
        year_text = f"{int(year):02d}" if len(year) == 2 else year
        return f"Zustellung {day:02d}.{month:02d}.{year_text}" if year_text else f"Zustellung {day:02d}.{month:02d}."
    return ""


def extract_delivery_carrier(*values: str) -> str:
    haystack = normalize_status_text(" ".join(values))
    carriers = {
        "dhl": "DHL",
        "hermes": "Hermes",
        "dpd": "DPD",
        "ups": "UPS",
        "gls": "GLS",
    }
    for marker, label in carriers.items():
        if marker in haystack:
            return label
    return ""


def is_suppressed_topic(*values: str, completed_topics: list[str] | None = None) -> bool:
    joined = " ".join(value or "" for value in values)
    haystack = normalize_status_text(joined)
    for entry in completed_topics or []:
        if completed_delivery_topic_matches(entry, joined, haystack):
            return True
    return False


def completed_delivery_topic_matches(entry: str, delivery_text: str, normalized_delivery_text: str) -> bool:
    normalized_entry = normalize_status_text(entry)
    if not normalized_entry:
        return False
    entry_order = extract_delivery_order_number(entry, "")
    delivery_order = extract_delivery_order_number(delivery_text, "")
    if entry_order or delivery_order:
        return bool(entry_order and delivery_order and entry_order == delivery_order)
    entry_tracking = extract_delivery_tracking_number("", entry)
    delivery_tracking = extract_delivery_tracking_number("", delivery_text)
    if entry_tracking or delivery_tracking:
        return bool(entry_tracking and delivery_tracking and entry_tracking == delivery_tracking)
    entry_tokens = meaningful_completed_topic_tokens(normalized_entry)
    if not entry_tokens:
        return False
    if normalized_entry in normalized_delivery_text:
        return len(entry_tokens) >= 2 or len(entry_tokens[0]) >= 10
    matched_tokens = sum(1 for token in entry_tokens if token in normalized_delivery_text)
    if len(entry_tokens) == 1:
        return len(entry_tokens[0]) >= 10 and matched_tokens == 1
    return matched_tokens >= min(3, len(entry_tokens))


def meaningful_completed_topic_tokens(normalized_entry: str) -> list[str]:
    generic_tokens = {
        "amazon",
        "bestellung",
        "bestellt",
        "bestellnummer",
        "bestsecret",
        "cody",
        "dhl",
        "erhalten",
        "erledigt",
        "geliefert",
        "golighter",
        "hermes",
        "lieferung",
        "paket",
        "sendung",
        "versand",
        "versendet",
        "wellster",
        "zugestellt",
    }
    return [
        token
        for token in normalized_entry.split()
        if len(token) >= 4 and token not in generic_tokens
    ]


def delivery_completion_addresses(sender_email: str, recipient_email: str) -> set[str]:
    addresses = {address for address in extract_email_addresses(f"{sender_email} {recipient_email}") if address}
    expanded = set(addresses)
    for address in addresses:
        local, separator, domain = address.partition("@")
        if separator and "+" not in local:
            expanded.add(f"{local}+cody@{domain}")
    return expanded


def delivery_completion_request_addresses(sender_email: str, recipient_email: str) -> set[str]:
    """Return addresses that should receive manual delivery-completion notes.

    Do not include the plain recipient address here. Normal delivery mails are
    also sent there and may contain words like "zugestellt" for a future ETA.
    """

    addresses = {address for address in extract_email_addresses(sender_email) if address}
    for address in extract_email_addresses(recipient_email):
        local, separator, domain = address.partition("@")
        if separator:
            addresses.add(address if "+cody" in local else f"{local}+cody@{domain}")
    return addresses


def extract_email_addresses(value: str) -> set[str]:
    addresses = {address.lower() for _, address in email.utils.getaddresses([value]) if address}
    addresses.update(match.lower() for match in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", value))
    return addresses


def message_references_any_address(value: str, addresses: set[str]) -> bool:
    haystack = value.lower()
    return any(address in haystack for address in addresses)


def extract_completed_delivery_topics_from_text(text: str) -> list[str]:
    topics = []
    patterns = (
        re.compile(
            r"\bcody[\s,:;-]*(?:lieferung|paket|bestellung|sendung)?\s*"
            r"(?:erledigt|zugestellt|geliefert|angekommen|erhalten|ist da)\s*[:\-]?\s*([^\n\r]+)",
            flags=re.I,
        ),
        re.compile(
            r"\bcody[\s,:;-]+([^\n\r]{4,120}?)\s+"
            r"(?:ist\s+)?(?:erledigt|zugestellt|geliefert|angekommen|erhalten|da)\b",
            flags=re.I,
        ),
        re.compile(
            r"(?:^|[\n\r])\s*(?:lieferung|paket|bestellung|sendung)\s+"
            r"(?:erledigt|zugestellt|geliefert|angekommen|erhalten|ist da)\s*[:\-]?\s*([^\n\r]+)",
            flags=re.I,
        ),
        re.compile(
            r"(?:^|[\n\r])\s*([^\n\r]{4,120}?)\s+"
            r"(?:ist\s+)?(?:erledigt|zugestellt|geliefert|angekommen|erhalten|da)\b",
            flags=re.I,
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            if looks_like_future_delivery_completion_false_positive(match.group(0)):
                continue
            topic = clean_completed_delivery_topic(match.group(1))
            if topic and topic not in topics:
                topics.append(topic)
    return topics


def looks_like_future_delivery_completion_false_positive(value: str) -> bool:
    normalized = normalize_status_text(value)
    future_markers = (
        "voraussichtlich",
        "wird ihnen",
        "wird dir",
        "wird zugestellt",
        "am montag",
        "am dienstag",
        "am mittwoch",
        "am donnerstag",
        "am freitag",
        "am samstag",
        "am sonntag",
    )
    return "zugestellt" in normalized and any(marker in normalized for marker in future_markers)


def clean_completed_delivery_topic(value: str) -> str:
    topic = strip_long(clean_mail_excerpt(value), 120)
    topic = re.split(r"\s+(?:\n|--|sent from|von meinem)", topic, maxsplit=1, flags=re.I)[0].strip()
    topic = re.sub(r"^(?:cody|hi cody|hallo cody|an cody)\s*[:,;-]?\s*", "", topic, flags=re.I).strip()
    topic = re.sub(
        r"^(?:lieferung|paket|bestellung|sendung)\s*(?:ist\s*)?",
        "",
        topic,
        flags=re.I,
    ).strip(" :-")
    return topic


def normalize_status_text(value: str) -> str:
    normalized = str(value or "").lower()
    normalized = re.sub(r"[\u2010-\u2015‑–—−-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = re.sub(r"[\u2010-\u2015‑–—−-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_delivery_key(subject: str, sender: str, text: str = "") -> str:
    sender_match = re.search(r"@([^>\s]+)", sender)
    sender_domain = sender_match.group(1).lower() if sender_match else sender.lower()
    tracking_number = extract_delivery_tracking_number(subject, text)
    if tracking_number:
        return f"tracking:{tracking_number}"
    order_number = extract_delivery_order_number(subject, text)
    if order_number:
        return f"{sender_domain}:order:{order_number}"
    product = extract_delivery_product_title(subject, text)
    if product:
        return f"{sender_domain}:product:{normalize_status_text(product)[:80]}"
    inferred = infer_delivery_product_title(subject, text)
    if inferred:
        return f"{sender_domain}:product:{normalize_status_text(inferred)[:80]}"
    cleaned = clean_delivery_subject(subject)
    cleaned = re.sub(r"\d+", "#", cleaned)
    return f"{sender_domain}:{cleaned[:80]}"


def extract_delivery_order_number(subject: str, text: str) -> str:
    haystack = f"{subject} {text[:2500]}"
    patterns = (
        r"\b(O-\d{4}-\d{6,})\b",
        r"\b(\d{3}-\d{7}-\d{7})\b",
        r"#\s*(\d{4,})",
        r"\bbestell(?:ung|nummer)?\D{0,20}(\d{4,})",
        r"\border\D{0,20}(\d{4,})",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack, flags=re.I)
        if match:
            return match.group(1)
    return ""


def extract_delivery_tracking_number(subject: str, text: str) -> str:
    haystack = f"{subject} {text[:3500]}"
    patterns = (
        r"\bsendungs(?:nummer|nr\.?)\D{0,40}([A-Z]?\d[A-Z0-9]{9,34})",
        r"\bpiececode=([A-Z]?\d[A-Z0-9]{9,34})",
        r"#([A-Z]\d{10,34})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, haystack, flags=re.I)
        if match:
            return match.group(1).upper()
    return ""


def extract_delivery_product_title(subject: str, text: str = "") -> str:
    candidates = re.findall(r"[„\"]([^“\"]{4,140})[“\"]", f"{subject} {text[:1500]}")
    candidates.extend(re.findall(r"\[([^\[\]]{8,180})\]\(https?://[^\s)]+/dp/", text[:2500], flags=re.I))
    for candidate in candidates:
        title = clean_mail_excerpt(candidate)
        if title and not is_truncated_delivery_title(title) and is_plausible_product_title(title):
            return strip_long(title, 110)
    return ""


def is_plausible_product_title(title: str) -> bool:
    normalized = normalize_status_text(title)
    generic_labels = {
        "meine bestellungen",
        "mein konto",
        "erneut kaufen",
        "bestellung ansehen oder ändern",
        "bestellung ansehen oder andern",
        "amazon de",
    }
    return bool(normalized) and normalized not in generic_labels


def is_truncated_delivery_title(value: str) -> bool:
    cleaned = clean_mail_excerpt(value)
    return bool(re.search(r"(?:\.\.\.|…)[\s\"'“”]*$", cleaned))


def clean_delivery_subject(subject: str) -> str:
    cleaned = re.sub(r"\b(re|aw|fwd|wg):\s*", "", subject, flags=re.I)
    cleaned = re.sub(
        r"^(versendet|versandt|verschickt|bestellt|geliefert|zugestellt|in zustellung|"
        r"bestätigung deiner bestellung|bestellung ist versandbereit)\s*:?\s*",
        "",
        cleaned,
        flags=re.I,
    )
    return normalize_status_text(cleaned)


def delivery_display_title(subject: str, sender: str, text: str) -> str:
    order_number = extract_delivery_order_number(subject, text)
    merchant = delivery_merchant_name(subject, sender, text)
    product = extract_delivery_product_title(subject, text)
    if product and merchant and "amazon" in normalize_status_text(merchant):
        return product
    if order_number and merchant:
        return f"{merchant} #{order_number}"
    if product:
        return product
    inferred = infer_delivery_product_title(subject, text)
    if inferred:
        return inferred
    if merchant and (is_truncated_delivery_title(subject) or "amazon" in normalize_status_text(merchant)):
        return f"{merchant}-Bestellung"
    return strip_long(clean_mail_excerpt(clean_delivery_subject(subject)), 90)


def infer_delivery_product_title(subject: str, text: str) -> str:
    haystack = normalize_status_text(f"{subject} {text[:2000]}")
    if "bestsecret" in haystack or "best secret" in haystack:
        if any(marker in haystack for marker in ("sendung", "lieferung", "versandbereit", "paket")):
            return "BestSecret Sendung"
    if "golighter" in haystack or "wellster" in haystack:
        if any(marker in haystack for marker in ("medikament", "sendung", "lieferung", "apotheke")):
            return "GoLighter/Wellster Medikament"
    if "tragbare" in haystack and any(marker in haystack for marker in ("fußball", "fussball")) and "schultasche" in haystack:
        return "Tragbare Fußballschuhtasche"
    if any(marker in haystack for marker in ("fußball", "fussball")) and "schuh" in haystack and "tasche" in haystack:
        return "Fußballschuhtasche"
    sender_subject_match = re.search(
        r"\b(?:ihre|deine)\s+(.{2,80}?)\s+sendung\b",
        subject,
        flags=re.I,
    )
    if sender_subject_match:
        sender_name = clean_mail_excerpt(sender_subject_match.group(1))
        if sender_name and normalize_status_text(sender_name) not in {"dhl", "paket"}:
            return f"{sender_name} Sendung"
    return ""


def delivery_merchant_name(subject: str, sender: str, text: str) -> str:
    haystack = normalize_status_text(f"{subject} {sender} {text[:600]}")
    if "kaffeetraum" in haystack:
        return "Kaffeetraum"
    if "amazon" in haystack:
        return "Amazon"
    if "bestsecret" in haystack or "best secret" in haystack:
        return "BestSecret"
    if "golighter" in haystack or "wellster" in haystack:
        return "GoLighter/Wellster"
    sender_name = sender.split("<", 1)[0].strip().strip('"')
    return strip_long(sender_name, 40)


def clean_mail_excerpt(value: str) -> str:
    cleaned = html.unescape(value or "")
    cleaned = re.sub(r"[\u034f\u200b-\u200f\u202a-\u202e]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def strip_long(value: str, max_len: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean[: max_len - 3] + "..." if len(clean) > max_len else clean

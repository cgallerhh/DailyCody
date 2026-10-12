#!/usr/bin/env python3
"""Publish private monitor results as an Actions secret, then advance its baseline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from follow_up_snapshot import freshness_warning, parse_timestamp, validate_snapshot


def check_candidate(snapshot_path: Path, baseline_path: Path) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    payload = validate_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")), now)
    warning = freshness_warning(payload, now)
    if warning:
        raise ValueError(warning)
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if parse_timestamp(baseline["checked_until"]) > parse_timestamp(payload["checked_until"]):
            raise ValueError("Ein aelterer Snapshot darf den Vergleichsstand nicht ersetzen")
    return payload


def publish(snapshot_path: Path, baseline_path: Path, repo: str) -> dict:
    payload = check_candidate(snapshot_path, baseline_path)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # Calendar comparison state and any other local-only metadata stay on the Mac.
    handoff = {key: payload[key] for key in (
        "schema_version", "status", "checked_from", "checked_until", "generated_at", "coverage", "items"
    )}
    handoff["coverage"] = {key: payload["coverage"][key] for key in (
        "gmail", "google_calendar", "outlook_mail", "outlook_calendar"
    )}
    handoff["items"] = []
    for item in payload["items"]:
        handoff_item = {key: item[key] for key in (
            "id", "topic", "change", "relevance", "action", "observed_at", "expires_at"
        )}
        handoff_item["sources"] = []
        for source in item["sources"]:
            source_fields = {key: source[key] for key in ("kind", "id", "url")}
            if source["kind"] in {"gmail", "outlook_mail"}:
                source_fields["from"] = source["from"]
                if source.get("headers"):
                    source_fields["headers"] = {
                        key.lower(): value for key, value in source["headers"].items()
                        if key.lower() in {"list-id", "list-unsubscribe", "precedence", "auto-submitted"}
                    }
            handoff_item["sources"].append(source_fields)
        handoff["items"].append(handoff_item)
    result = subprocess.run(
        ["gh", "secret", "set", "FOLLOW_UP_SNAPSHOT_JSON", "--repo", repo, "--app", "actions"],
        input=json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
        text=True,
        capture_output=True,
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError(f"GitHub-Secret konnte nicht aktualisiert werden (Exit {result.returncode})")
    # Advance the baseline only after GitHub confirms the private handoff.
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=baseline_path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(baseline_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--repo", default="cgallerhh/DailyCody")
    parser.add_argument("--check-only", action="store_true", help="Validate freshness and format without publishing or changing the baseline")
    args = parser.parse_args()
    if args.check_only:
        payload = check_candidate(args.snapshot, args.baseline)
        print(f"Follow-up candidate valid: {payload['checked_until']}; {len(payload['items'])} items. Nothing published.")
        return 0
    payload = publish(args.snapshot, args.baseline, args.repo)
    print(f"Follow-up handoff confirmed: {payload['checked_until']}; {len(payload['items'])} items. Baseline saved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Follow-up handoff failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

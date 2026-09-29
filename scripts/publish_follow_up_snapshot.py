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

from follow_up_snapshot import parse_timestamp, validate_snapshot


def publish(snapshot_path: Path, baseline_path: Path, repo: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    payload = validate_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")), now)
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if parse_timestamp(baseline["checked_until"]) > parse_timestamp(payload["checked_until"]):
            raise ValueError("Ein aelterer Snapshot darf den Vergleichsstand nicht ersetzen")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    result = subprocess.run(
        ["gh", "secret", "set", "FOLLOW_UP_SNAPSHOT_JSON", "--repo", repo, "--app", "actions"],
        input=encoded,
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
    args = parser.parse_args()
    payload = publish(args.snapshot, args.baseline, args.repo)
    print(f"Follow-up handoff confirmed: {payload['checked_until']}; {len(payload['items'])} items. Baseline saved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Follow-up handoff failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

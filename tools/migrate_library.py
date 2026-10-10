"""Preview or apply validated legacy release-folder renames, with a private audit."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ytdlp_studio.backend import CONFIG_DIR  # noqa: E402
from ytdlp_studio.catalogue import request_catalogue  # noqa: E402
from ytdlp_studio.library import apply_renames, plan_legacy_renames  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--apply", action="store_true", help="Rename only after validating every folder and NAS file.")
    args = parser.parse_args()
    try:
        catalogue = request_catalogue("artist", args.artist, all_editions=True)
        changes = plan_legacy_renames(catalogue, args.local, args.mirror)
        for change in changes:
            print(f"{change.source} -> {change.destination} ({len(change.files)} files verified)")
        if not args.apply or not changes:
            print(f"{len(changes)} folder renames planned; no files changed.")
            return 0
        audit = CONFIG_DIR / "reports" / ("library-migration-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
        audit.parent.mkdir(parents=True, exist_ok=True)
        record = {"state": "validated", "changes": [asdict(change) for change in changes]}
        audit.write_text(json.dumps(record, default=str, indent=2), encoding="utf-8")
        try:
            apply_renames(changes)
            record["state"] = "completed"
        except (OSError, ValueError, RuntimeError) as error:
            record.update(state="failed; inspect paths before retrying", error=str(error))
            raise
        finally:
            audit.write_text(json.dumps(record, default=str, indent=2), encoding="utf-8")
        print(f"Renamed {len(changes)} folders. File hashes unchanged. Audit: {audit}")
        return 0
    except (OSError, ValueError, TimeoutError, RuntimeError) as error:
        print(f"Migration stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

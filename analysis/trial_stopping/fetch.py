"""Pull the fields the trial-stopping model needs for every interventional study.

Uses the package's own client with a `fields` selection, so each record carries only
status, design, sponsor, eligibility, outcome, location-country and MeSH data instead of
the full protocol + results document.

Output: data/trial_stopping/studies.jsonl.gz (one raw API record per line).
Resumable: re-running skips NCT IDs already in the output and appends.

Usage:
    python analysis/trial_stopping/fetch.py
    python analysis/trial_stopping/fetch.py --max-studies 2000   # smoke test
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

from tqdm import tqdm

from clinicaltrials_parser import ClinicalTrialsClient

OUT = Path("data/trial_stopping/studies.jsonl.gz")

FIELDS = ",".join(
    [
        "protocolSection.identificationModule",
        "protocolSection.statusModule",
        "protocolSection.sponsorCollaboratorsModule",
        "protocolSection.oversightModule",
        "protocolSection.descriptionModule.briefSummary",
        "protocolSection.conditionsModule",
        "protocolSection.designModule",
        "protocolSection.armsInterventionsModule",
        "protocolSection.outcomesModule",
        "protocolSection.eligibilityModule",
        "protocolSection.contactsLocationsModule.locations.country",
        "derivedSection.conditionBrowseModule",
        "derivedSection.interventionBrowseModule",
    ]
)

# Observational studies don't "stop early" in the same sense; the model covers
# interventional trials only.
QUERY = {"filter.advanced": "AREA[StudyType]INTERVENTIONAL"}


def _seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                seen.add(json.loads(line)["protocolSection"]["identificationModule"]["nctId"])
            except (json.JSONDecodeError, KeyError):
                break  # truncated last line from an interrupted run
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-studies", type=int, default=None)
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen = _seen_ids(OUT)
    written = 0
    with ClinicalTrialsClient(page_size=1000, rate_limit=2, timeout=120) as client:
        total = client.get_total_count(**QUERY)
        print(f"interventional total: {total:,}; already have: {len(seen):,}", flush=True)
        with gzip.open(OUT, "at", encoding="utf-8") as f, tqdm(total=total, initial=len(seen)) as bar:
            for study in client.iter_studies(fields=FIELDS, **QUERY):
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                if nct in seen:
                    continue
                f.write(json.dumps(study, separators=(",", ":")) + "\n")
                seen.add(nct)
                written += 1
                bar.update(1)
                if args.max_studies and written >= args.max_studies:
                    break
    print(f"wrote {written:,} new records -> {OUT} ({len(seen):,} total)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

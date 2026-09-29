# CLAUDE.md - ClinicalTrialsGovParser

Python package that fetches, parses, and exports ClinicalTrials.gov API v2 records
to JSONL/JSON/CSV/Parquet/DuckDB/Iceberg. See README.md for usage.

Tests: `pytest` (integration tests hit the live API and are skipped unless
`CTGOV_INTEGRATION_TESTS=1`).

## Live-API gotchas (verified 2026-09-29)

- Don't set a custom User-Agent: the site's WAF 403s every non-default UA tried; httpx's
  default passes.
- There is no `filter.phase` / `filter.studyType` (400). Use
  `filter.advanced=AREA[Phase](PHASE2 OR PHASE3) AND AREA[StudyType]INTERVENTIONAL`.
- httpx sends a `None` param as an empty value (`fields=`), which the API rejects.
- The unit tests mock `_request`, so API drift only shows up in the live suite — run it
  (`CTGOV_INTEGRATION_TESTS=1 pytest`) after touching client or CLI code.
- `analysis/trial_stopping/` is a research analysis, not part of the package; data goes to
  the gitignored `data/`.

## Session notes and task list live in a separate repo

Session notes and the task list for this project are NOT in this repo. They live in the
private `work-notes` repo, cloned as a sibling, at `work-notes/ClinicalTrialsGovParser/`:

    C:\Users\zande\PycharmProjects\work-notes\ClinicalTrialsGovParser\

When Zander asks to update session notes or the task list, edit the files there, not here.
This repo keeps only durable documentation (this file, `docs/`), so it can be public
without a visitor scrolling through a working log. See `work-notes/CLAUDE.md` for the
convention.

# CLAUDE.md - ClinicalTrialsGovParser

Python package that fetches, parses, and exports ClinicalTrials.gov API v2 records
to JSONL/JSON/CSV/Parquet/DuckDB/Iceberg. See README.md for usage.

Tests: `pytest` (integration tests hit the live API and are skipped unless
`CTGOV_INTEGRATION_TESTS=1`).

## Session notes and task list live in a separate repo

Session notes and the task list for this project are NOT in this repo. They live in the
private `work-notes` repo, cloned as a sibling, at `work-notes/ClinicalTrialsGovParser/`:

    C:\Users\zande\PycharmProjects\work-notes\ClinicalTrialsGovParser\

When Zander asks to update session notes or the task list, edit the files there, not here.
This repo keeps only durable documentation (this file, `docs/`), so it can be public
without a visitor scrolling through a working log. See `work-notes/CLAUDE.md` for the
convention.

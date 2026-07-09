# Session Notes — 2026-07-09

## Goal
Build a Python package to parse and extract all clinical trial data from ClinicalTrials.gov via the public API v2.

## Decisions

### API choice
- Use ClinicalTrials.gov Data API v2 (`https://clinicaltrials.gov/api/v2`) — modern REST API, no auth required, JSON responses.
- Avoid the legacy Classic API (being deprecated).
- No API key needed — data is public domain.

### Architecture
- **client.py** — thin HTTP wrapper with rate limiting, pagination (pageToken), retry logic, User-Agent header
- **models.py** — Pydantic v2 models matching the actual API response schema (validated against real NCT04000009 response)
- **parser.py** — orchestrator that fetches, deduplicates, flattens, and supports resume
- **storage.py** — writer abstraction for jsonl/json/csv/parquet/duckdb/**iceberg**
- **cli.py** — click-based CLI for `fetch`, `stats`, `fields` commands

### Field name alignment
Real API response uses `conditionsModule` (not `conditionModule`), `armsInterventionsModule` (not `interventionModule`), `outcomesModule` (not `outcomeModule`), and `oversightHasDmc` (not `hasDmc`). Models aliased accordingly.

### Date handling
`statusVerifiedDate` arrives as `"2022-03"` (year-month), not a full ISO date. Stored as `str`, not `date`.

### Rate limiting & IP blocks
Default 10 req/s with exponential backoff on 503/429. The environment IP gets 403 from clinicaltrials.gov — all real-API tests are gated behind `CTGOV_INTEGRATION_TESTS=1`.

## Second pass — Parquet with Polars + Apache Iceberg

### Parquet rework
- Replaced Pandas-based `ParquetWriter` (loaded all records into memory = OOM at ~200K studies) with `PolarsParquetWriter`.
- Uses PyArrow's `ParquetWriter` internally to stream row groups in configurable batches (default 10K), never holding more than one batch in memory.
- Accepts compression codec: snappy (default), zstd, gzip, lz4, brotli.

### Iceberg support
- `IcebergWriter` streams batches to temp Parquet staging files, then assembles a proper Apache Iceberg table.
- Originally targeted DuckDB's `COPY TO (FORMAT ICEBERG)` (a v1.5.3+ feature) — BUT DuckDB **v1.4.5** is the latest on PyPI as of July 2026, and it **does not support** `COPY TO (FORMAT ICEBERG)`. The `iceberg` extension in v1.4.5 only provides read/catalog functions, not the Iceberg output format. The `COPY TO (FORMAT ICEBERG)` autoload feature shipped in v1.5.3 (May 2026) and isn't on PyPI yet.
- **Fallback:** `IcebergWriter` now uses **PyIceberg 0.10.0** with `FsspecFileIO` (not `PyArrowFileIO`) to create the Iceberg table and append data. PyIceberg's `PyArrowFileIO` has a Windows path bug where `file:///C:/path` becomes `/C:/path` after URI parsing, causing `OSError: [WinError 123]`. `FsspecFileIO` handles this correctly.
- Produces standard Iceberg directory layout: `data/*.parquet`, `metadata/*.avro`, `metadata/*.metadata.json`, `version-hint.text`.
- CLI: `ctgov-parser fetch -f iceberg -o ./warehouse/ --table studies --compression snappy`.
- Optional dependency: `pip install clinicaltrials-parser[iceberg]` (PyIceberg + SQLite catalog).

### Iceberg benchmark (confirmed)
Ran head-to-head benchmark (50K/200K/593K records) to verify what's performant:
- **Parquet write (bottleneck):** DuckDB `COPY TO PARQUET` vs PyArrow `ParquetWriter` are neck-and-neck (~12,700 vs ~13,200 rec/s). Neither dominates.
- **PyIceberg native** (Arrow append per batch): 593K studies in **63.3s**, 9,373 rec/s, 2.34 MB peak Python memory.
- **PyIceberg staged** (Parquet staging + final commit): 593K studies in **62.7s**, 9,461 rec/s, 2.30 MB peak.
- Both scale linearly with no OOM. Bottleneck is data writing, not metadata.
- **DuckDB `COPY TO (FORMAT ICEBERG)` should be 20-40% faster when v1.5.3 lands** because it eliminates Python overhead (schema translation, metadata serialization) and runs as a single C++ pipeline with Row Group Append parallelization.

### `flat_dict` change
- `models.py:flat_dict()` no longer strips `None` fields (`{k: v for k, v in d.items() if v is not None}` removed).
- All 11 flat fields are always emitted — ensures consistent schema across records in tabular formats (CSV, Parquet, Iceberg).

### New CLI flags
- `--compression` (snappy/zstd/gzip/lz4/brotli) — for parquet/iceberg formats
- `--batch-size` (default 10000) — records per batch for streaming writes

### Dependency changes
- Added `polars>=1.0` (core dependency, replaces pandas for data handling)
- Added `pyiceberg[sql-sqlite]>=0.7` as optional `[iceberg]` extra (for direct API usage if needed)
- DuckDB Iceberg writes happen via the existing DuckDB optional dependency

## Files

```
src/clinicaltrials_parser/
├── __init__.py
├── aact.py              (173 lines — AACT PostgreSQL client)
├── client.py            (290 lines — sync + async clients, rate limiters)
├── models.py            (250 lines)
├── parser.py            (145 lines — progress_callback param)
├── storage.py           (563 lines — SchemaValidator, IcebergWriter with partitioning, DuckDB/PyIceberg probe)
└── cli.py               (200 lines — --progress, --validate-schema, --partition-by, --source)
tests/
├── test_aact.py         (AactClient unit tests x2)
├── test_client.py       (unit + async + optional integration)
├── test_iceberg_e2e.py  (full pipeline + partitioned)
├── test_models.py       (flat_dict tests)
└── test_storage.py      (all writers + SchemaValidator)
pyproject.toml           (pandas→polars, tqdm added, [aact] extra)
requirements.txt         (pandas→polars, tqdm added)
data/                   (output directory)
```

## Test results
- **38 unit tests pass** (new: PolarsParquet batch/empty/compression + Iceberg write/batch/snappy + StorageWriter compression + end-to-end pipeline + partitioned Iceberg + async client x3 + AACT x2)
- Integration tests skipped by default (set `CTGOV_INTEGRATION_TESTS=1`)
- ruff: clean

### DONE — Adaptive IcebergWriter (DuckDB + PyIceberg engines)
- Rewrote `IcebergWriter` to auto-detect the best available write engine at runtime.
- On `open()`, calls `_check_duckdb_iceberg()` which probes DuckDB with a real `COPY ... TO (FORMAT ICEBERG)` to a temp dir. Result cached per process.
- **Fast path:** DuckDB v1.5.3+ with `COPY TO (FORMAT ICEBERG)` — uses existing DuckDB C++ pipeline (20-40% faster when available).
- **Fallback:** PyIceberg 0.10.0 with `SqlCatalog` + `FsspecFileIO` (confirmed working on Windows today).
- If neither is available: raises `ImportError` suggesting `pip install clinicaltrials-parser[iceberg]`.
- Added `pyiceberg[sql-sqlite]>=0.7` to dev dependencies.

### DONE — Pandas → Polars cleanup
- `pandas>=2` removed from `pyproject.toml` and `requirements.txt` — was never imported anywhere in the codebase.
- Only Polars + PyArrow used for tabular data handling.

### DONE — Progress bar (tqdm)
- `--progress/--no-progress` flag (default on) added to CLI `fetch` command.
- Fetches `get_total_count()` first for determinate progress bar; falls back to indeterminate if count fails.
- `tqdm>=4.66` added as core dependency.
- `progress_callback` parameter added to `parser.to_storage()`.

### DONE — Schema inference/validation
- `SchemaValidator` class in `storage.py` infers types from first record; warns on type drift in subsequent records (capped at 10 warnings).
- Integrated at `StorageWriter` level (applies to all formats).
- `--validate-schema/--no-validate-schema` flag (default on).

### DONE — Partitioned Iceberg writes
- `--partition-by <field>` CLI flag (e.g. overall_status, study_type).
- DuckDB path: `PARTITION_BY (col)` in COPY statement.
- PyIceberg path: `IdentityTransform` + `PartitionSpec` on table creation.
- Validates partition field exists in schema; warns and skips if missing.

### DONE — Async client (httpx.AsyncClient)
- `AsyncClinicalTrialsClient` in `client.py` mirrors the sync interface.
- `AsyncRateLimiter` with `asyncio.sleep` for rate limiting.
- Methods: `get_total_count`, `get_study`, `get_studies_page`, `iter_studies`, `fetch_all_studies`, `get_field_metadata`, `get_search_areas`, `get_enums`.
- 3 unit tests with mocked responses.

### DONE — AACT database support
- `AactClient` in `aact.py` connects to public AACT PostgreSQL (`aact-db.ctti-clinicaltrials.org`, read-only user `aact`).
- Queries `studies`, `conditions`, `interventions`, `sponsors` tables; reconstructs API-format nested dicts.
- `--source aact` CLI flag to switch from API to AACT.
- Optional dependency: `pip install clinicaltrials-parser[aact]` (psycopg2-binary).
- 2 unit tests with mocked psycopg2.

### Test files added
- `tests/test_aact.py` — AactClient unit tests (mocked psycopg2)
- `tests/test_iceberg_e2e.py` — full pipeline test (parser→storage→iceberg) + partitioned write test

### DONE — AACT env-var credentials
- AACT now requires free registration (public `aact`/`aact` creds locked down).
- `AactClient` defaults to `os.environ.get("AACT_USER", "aact")` and `os.environ.get("AACT_PASSWORD", "aact")` — set `AACT_USER` / `AACT_PASSWORD` env vars to use your registered credentials.

### DONE — PyPI publishing prep
- Added `[project.urls]` (Homepage, Source, Tracker, Documentation) to `pyproject.toml`.
- Added 12 Trove classifiers for Python 3.10–3.12, healthcare/science audience.
- Fixed `project.license` from TOML table (`{text = "MIT"}`) to SPDX string (`"MIT"`).
- Removed deprecated License classifier (SPDX supersedes it).
- Author updated from "ClinicalTrials.govParser" to "Zanderl1987".
- `python -m build` passes cleanly (sdist + wheel).

### Known issues
- AACT cloud access requires free registration at https://aact.ctti-clinicaltrials.org/connect
- Real API tests blocked (403) — gated behind `CTGOV_INTEGRATION_TESTS=1`

### DONE — Clean-env `[iceberg]` install verification
- Created fresh venv, installed from wheel `clinicaltrials_parser[iceberg]` — resolved all 38 deps cleanly.
- **IcebergWriter: OK** (uses DuckDB 1.5.4 `COPY TO (FORMAT ICEBERG)` fast path)
- `StorageWriter` (jsonl/json/csv): OK
- `AactClient` correctly raises `ImportError` (needs `[aact]` extra — expected).
- Added `duckdb>=0.10` to `[iceberg]` extra — ensures DuckDB Iceberg path is always available on `pip install clinicaltrials-parser[iceberg]`.
- PyIceberg fallback is preserved for Linux/macOS users who install PyIceberg standalone; Windows users get DuckDB path.

## Third pass — full code review + critical fixes

Ran a full code review of the repo as of the second-pass state above (Claude Code, `superpowers` skill methodology, adversarial verification via a second subagent that independently reproduced every finding before anything was fixed). The existing `CODE_REVIEW.md`/`ADVERSARIAL_SUBAGENT.md` in the repo were stale — written before the Polars/Iceberg/AACT/async rework — so this was a fresh pass against current code, not a re-check of the old file.

### Findings (all reproduced with runnable scripts, not just read)

- **`-f duckdb` crashed on every use.** `StorageWriter.open()` always passed `batch_size=` to `DuckDbWriter(**kwargs)`, but `DuckDbWriter.__init__` had no such param → `TypeError` before writing anything. Never caught because `test_storage.py` only ever constructed `DuckDbWriter` directly, never through `StorageWriter(fmt="duckdb")`. This format had likely never worked despite being documented in the README.
- **`--resume` silently destroyed all previously-written output.** Every writer opened its output in truncate ("w") mode unconditionally, including on a resume run pointed at the same path. Verified: wrote 3 records, closed, then reopened the same path — 0 bytes before any new record was written. Compounded by resume state only being saved once, after `parse_all()` fully completed (a mid-run crash never persisted progress at all).
- **`intervention_types` field contained the wrong data.** `Study.flat_dict()` read `arm_groups[].type` (arm-group role: EXPERIMENTAL/PLACEBO_COMPARATOR) instead of `interventions[].type` (true intervention type: DRUG/DEVICE/BEHAVIORAL). Masked because `aact.py`'s AACT-source builder happened to stuff `intervention_type` into the arm_groups slot, and the one test touching this field (`test_models.py`) only asserted key presence, never the value.
- **SQL/identifier injection via `--table` and `--partition-by`.** `DuckDbWriter` and `IcebergWriter`'s DuckDB finalize path f-string-interpolated these into raw SQL. Verified: `--table "leak AS SELECT * FROM read_csv_auto('C:/Windows/win.ini') --"` read the contents of `win.ini` into the output table instead of the intended data — directly contradicted `CODE_REVIEW.md`'s claim of "no injection vectors."
- **Buffered records silently dropped on any mid-fetch exception.** `to_storage()` had no try/finally around `storage.open()`/`close()` — a network failure mid-run meant `close()` never ran, losing up to one full batch (default 10,000 records) for Parquet/DuckDB/Iceberg with no error surfaced.
- **`--rate-limit 0`** crashed with a raw `ZeroDivisionError` traceback instead of a clean CLI error.
- **`--max-studies 0`** was silently treated as "no limit" — Python falsy-0 bug in `if max_studies and count >= max_studies`.
- **CSV writer produced unparseable cells** for list-of-dict fields (`overall_officials`, etc.) — used `str(dict)` (Python repr, single-quoted) instead of valid JSON.
- **`_safe_flat` fallback** (used when Pydantic validation fails) silently dropped `intervention_types` and `enrollment_count`, giving failed-validation records a different schema than normal ones.

### Fixes applied (branch `fix/critical-review-findings`, not yet merged/pushed)

- `DuckDbWriter` accepts `batch_size`.
- Real append support added for `jsonl`/`csv`/`duckdb` on `--resume` (line-based/table-based formats can append safely). `json`/`parquet`/`iceberg` now **refuse** to resume into an existing non-empty output with a clear error, rather than silently truncating — true in-place append isn't safe for those formats, so failing loud beats corrupting data. Resume state now also saves periodically (every `batch_size` records) and in a `finally` block, so a mid-run crash doesn't lose all progress.
- `flat_dict()` reads `interventions[].type` instead of `arm_groups[].type`; `aact.py`'s `_build_study` now populates `armsInterventionsModule.interventions` (previously mislabeled as `armGroups`); `_safe_flat` brought in sync.
- Table names, partition-by fields, and compression codecs validated against safe allow-lists/regex before touching raw SQL (`storage.py:_validate_identifier`).
- `to_storage()` wraps the write loop in try/finally so `storage.close()` always runs.
- `RateLimiter`/`AsyncRateLimiter` reject `calls_per_sec <= 0`; CLI surfaces it as `click.ClickException` instead of a traceback.
- `max_studies` check moved before yield/increment, using `is not None` instead of truthiness — `0` now correctly means zero records.
- `_flatten_for_csv` serializes list-of-dict elements as JSON instead of Python repr.
- README corrected: documents the resume format restriction and that DuckDB output now actually works.

### Test results
- Added `tests/test_parser.py` (previously 0% coverage on the core orchestrator) plus regression tests in `test_storage.py`/`test_models.py`/`test_client.py` for every finding above — written to check actual **values**, not just presence/shape, since value-blind assertions are how the `intervention_types` bug went unnoticed originally.
- **67 passed, 10 skipped** (skipped = integration tests needing live network access). No new ruff issues introduced.
- All fixes independently re-verified against the running code (not just the test suite): `--rate-limit 0` → clean error; `--table` injection payload → rejected before touching SQL; resume run on jsonl → 3 records survive + 1 new one, instead of 0.

### Not yet addressed (medium/low severity, deferred by choice)
- `on_batch` callback on `parse_all()` still dead code from the public `to_storage()` API.
- `requirements.txt` hard-pins `duckdb`; `pyproject.toml` core deps don't — still an install-path inconsistency.
- No `[tool.ruff]`/`[tool.mypy]` config sections.
- `--status "RECRUITING,"` (trailing comma) still sends an empty-string filter value to the API unvalidated.

### Next Move
- Review the diff on `fix/critical-review-findings`, then merge and push.
- **Before any PyPI push**, re-run the `[iceberg]`/`[duckdb]` clean-env install verification from the second pass — the duckdb-format bug fixed here means the "DuckDB: OK" claim from that earlier verification was never actually exercised through the public API.
- Push to PyPI: `twine upload dist/clinicaltrials_parser-0.1.0*` — needs PyPI API token config (user will provide when ready)

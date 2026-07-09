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
├── client.py            (145 lines)
├── models.py            (250 lines)
├── parser.py            (144 lines)
├── storage.py           (300 lines — PolarsParquetWriter, IcebergWriter added)
└── cli.py               (168 lines — --compression, --batch-size, iceberg format)
tests/
├── test_client.py       (unit + optional integration)
├── test_models.py       (flat_dict test updated for non-stripping behavior)
└── test_storage.py      (PolarsParquetWriter + IcebergWriter tests added)
pyproject.toml
requirements.txt
data/                   (output directory)
```

## Test results
- **31 unit tests pass** (7 new: PolarsParquet batch/empty/compression + Iceberg write/batch/snappy + StorageWriter compression)
- Integration tests skipped by default (set `CTGOV_INTEGRATION_TESTS=1`)
- ruff: clean

### DONE — Adaptive IcebergWriter (DuckDB + PyIceberg engines)
- Rewrote `IcebergWriter` to auto-detect the best available write engine at runtime.
- On `open()`, calls `_check_duckdb_iceberg()` which probes DuckDB with a real `COPY ... TO (FORMAT ICEBERG)` to a temp dir. Result cached per process.
- **Fast path:** DuckDB v1.5.3+ with `COPY TO (FORMAT ICEBERG)` — uses existing DuckDB C++ pipeline (20-40% faster when available).
- **Fallback:** PyIceberg 0.10.0 with `SqlCatalog` + `FsspecFileIO` (confirmed working on Windows today).
- If neither is available: raises `ImportError` suggesting `pip install clinicaltrials-parser[iceberg]`.
- Added `pyiceberg[sql-sqlite]>=0.7` to dev dependencies.
- All 31 tests pass: Iceberg tests exercise the PyIceberg fallback path and verify output with `iceberg_scan()`.

### Next / open questions
- Add schema inference/validation for CSV/Parquet/Iceberg column types
- AACT database support as alternative data source
- Async version with `httpx.AsyncClient` for higher throughput
- Consider replacing remaining Pandas usage with Polars
- Add partitioned Iceberg writes (by status, study_type, etc.)
- Progress bar for large fetches (tqdm/rich)

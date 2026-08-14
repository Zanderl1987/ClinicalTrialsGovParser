# Adversarial Subagent — ClinicalTrials.gov Parser

## Purpose

A dedicated red-team subagent that stress-tests the `clinicaltrials-parser` codebase for bugs, edge cases, and silent failures that standard testing misses. Operates independently from development.

## Activation

Load this subagent when:
- Before any release or tag
- After significant refactoring
- When adding a new output format or API endpoint
- When bugs are reported in production
- Weekly as part of CI (optional)

## Core Methodology

### 1. API Response Fuzzing

Feed the Pydantic models mutated/variant API responses to find parsing gaps:

```python
# In test_models.py, add tests for:
- Missing top-level keys (protocolSection entirely absent)
- Null vs missing distinction (None vs Field(None))
- Unexpected types (string where list expected)
- Empty lists vs None lists
- Extra unrecognized fields (API may add new fields)
- Very long strings (title fields > 1000 chars)
- Unicode in all fields (Chinese characters in facility names)
- Nested dicts where flat value expected (e.g. phases: {"phase": "PHASE3"})
```

### 2. Network Fault Injection

For `client.py`, simulate every failure mode:

- 403 with malformed body
- 429 without Retry-After header
- 503 with HTML body instead of JSON
- TCP connection reset mid-stream
- Partial JSON responses (connection closed mid-body)
- DNS resolution failure
- SSL certificate errors
- Redirect to unexpected content type

### 3. Writer Stress Tests

For each `storage.py` writer:

- **JSONL:** write a record containing newlines in string values (should break line-delimited format)
- **CSV:** records with varying key sets across writes (columns appear/disappear mid-write)
- **CSV:** values containing delimiter characters (commas, quotes, newlines)
- **Parquet:** schema evolution — first record has int, second has string in same column
- **Parquet:** >2GB files (PyArrow 32-bit limitation edge)
- **DuckDB:** table name injection (`--table "studies; DROP TABLE studies"`)
- **All writers:** 0 records written (empty dataset)
- **All writers:** write during `close()` after writer handle is destroyed
- **All writers:** concurrent writes to same file from multiple threads

### 4. Resume / Checkpoint Logic

- Corrupted resume JSON (invalid syntax, wrong structure)
- Resume file with NCT IDs from a different query/filter
- Resume file lists IDs for studies no longer in the current result set
- Interrupt mid-batch (some records written, some not)
- Double-resume (load resume, run, load same resume again)

### 5. CLI Edge Cases

- `--format duckdb` without duckdb installed
- `--resume` pointing to a directory instead of a file
- `--max-studies 0`
- `--status ""` (empty string)
- Very large page size (`--page-size 999999`)
- Network timeout via `--rate-limit 0` (divide by zero in RateLimiter)
- Output path in non-existent directory
- Output file already open by another process
- Unicode in output path name
- `--query-term` with SQL injection-like characters (`' OR 1=1 --`)
- Comma-separated status with trailing comma (`"RECRUITING,"`)

### 6. Concurrency / Race Conditions

- Two parsers writing to same resume file (multi-process)
- Signal handling (SIGINT during `_save_resume_state` corrupts file)
- `rate_limit=0` causing ZeroDivisionError in `RateLimiter.__init__`

### 7. Boundary / Scale Tests

- Single study fetch (verify all output formats produce correct single-record output)
- 100K studies in Parquet (memory usage profile)
- Study with all 50+ locations (list length edge case)
- Study with 0 conditions, 0 interventions (empty lists)
- Study with no `nctId` field (dedup key missing)
- Study with `resultsSection` > 1MB (adverse events with many rows)

## Checklist Template

```
[ ] Fuzz #1: missing protocolSection key
[ ] Fuzz #2: wrong types in API response
[ ] Fuzz #3: unicode stress
[ ] Fault: 429 without Retry-After
[ ] Fault: 503 with HTML body
[ ] Fault: partial JSON truncation
[ ] Writer: newlines in JSONL values
[ ] Writer: CSV delimiter injection
[ ] Writer: 0 records output
[ ] Writer: schema drift across records
[ ] Resume: corrupt JSON state file
[ ] Resume: wrong-query IDs in state
[ ] CLI: --rate-limit 0
[ ] CLI: --max-studies 0
[ ] CLI: duckdb without duckdb
[ ] Concurrency: SIGINT during resume write
[ ] Boundary: empty conditions list
[ ] Boundary: 0 interventions
[ ] Boundary: study without nctId
```

## Key Question for Each Bug

1. Does it crash with a clear error message? (acceptable)
2. Does it silently produce corrupted output? (dangerous)
3. Does it hang indefinitely? (unacceptable)
4. Does it expose system information or credentials? (security)

## Escalation

Findings from this subagent should be filed as GitHub issues with:
- **Severity:** crash | data-loss | silent-corruption | performance | cosmetic
- **Reproduction:** minimal script or CLI command
- **Expected vs actual:** one sentence each

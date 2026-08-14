# Code Review — ClinicalTrials.gov Parser v0.1.0

Review date: 2026-07-09 | Coverage: all 5 modules + tests + build config

---

## Critical Issues

### C1 — `AsyncIterator` type hint on sync generator (`client.py:5,120`)

```python
from typing import Any, AsyncIterator
...
def iter_studies(self, **params: Any) -> AsyncIterator[dict[str, Any]]:
```

`iter_studies` is a synchronous generator (uses `yield`, no `async`), but its return annotation says `AsyncIterator`. This is misleading — callers will get a `typing.AsyncIterator` at type-check time but a sync generator at runtime. If someone writes `async for study in client.iter_studies()` it will fail at runtime.

**Fix:** change to `Iterator[dict[str, Any]]`.

### C2 — CSV writer fails on list-of-dicts fields (`storage.py:71-83`)

`_flatten_for_csv` joins list values with `"; "`, but if a value is a `list[dict]` (e.g. `overallOfficials`, `locations`), `str(x)` produces `"{'name': ...}"` — unparseable Python repr strings in CSV cells.

**Fix:** handle `list[dict]` by serializing to JSON string, or skip deeply nested structures.

### C3 — `_safe_flat` fallback drops fields (`parser.py:97-115`)

When Pydantic parsing fails, `_safe_flat` extracts a subset of fields but misses `intervention_types` and `enrollment_count` compared to the real `flat_dict()`. The two code paths diverge silently — a study that fails Pydantic validation gets a different schema than one that passes.

**Fix:** either make `_safe_flat` match `flat_dict` exactly, or don't use a fallback — log a warning and skip the record so output is consistent.

### C4 — Parquet/DuckDB hold all records in memory (`storage.py:113,133`)

`ParquetWriter` and `DuckDbWriter` append to `self._records: list[dict]` and only write on `close()`. For 593K studies this will **OOM** — each study ~10 KB → ~6 GB in memory.

**Fix:** write to Parquet in batches using `pd.DataFrame.to_parquet` with `append=True` (requires PyArrow) or write to a temp directory and concatenate at the end. For DuckDB, insert each batch with `con.executemany` or `con.execute("INSERT INTO table SELECT * FROM df")` per batch.

### C5 — Resume state saved only at end of entire fetch (`parser.py:95`)

`_save_resume_state()` is called once after `parse_all()` finishes. If the process crashes 10 hours in, **all progress is lost** — the resume file is never written mid-stream.

**Fix:** save resume state periodically (every N records or every batch callback) inside `parse_all`, or after each page.

---

## Medium Issues

### M1 — `pageSize` kwarg collision (`client.py:111`)

```python
params_out.setdefault("pageSize", self.page_size)
```

The method signature is `get_studies_page(self, page_token: str | None = None, **params: Any)`. If a caller passes `pageSize=50` in `params`, it silently overrides the `pageSize` key in `params_out` (wrong order — should set after merging).

Also, the `iter_studies` method passes all kwargs through, so callers must know the raw API parameter names (`filter.overallStatus`) rather than nicer Python names.

**Fix:** pop known params before defaulting, or use explicit keyword arguments for all query parameters.

### M2 — `StudyParser` test coverage missing (`parser.py`)

There are zero tests for `StudyParser.parse_all`, `to_storage`, `_safe_flat`, or resume state. This is the core orchestrator module.

### M3 — `DuckDbWriter` import buried in `close()` (`storage.py:145`)

```python
def close(self) -> None:
    if not self._records:
        return
    try:
        import duckdb
```

The duckdb import is inside `close()`, so a user who writes 500K records only discovers duckdb is missing on close() — after 30 minutes of fetching.

**Fix:** import in `__init__` or `open()` so it fails fast.

### M4 — `on_batch` callback type is `Callable[[list[dict[str, Any]]], None]` but `to_storage` never uses it (`parser.py:53,114-121`)

`to_storage` calls `parse_all` without `on_batch`, so the batch callback mechanism is dead code unless the user calls `parse_all` directly. Consider consolidating batch-writing into `to_storage` directly.

### M5 — `statusVerifiedDate` stored as `str` — no validation (`models.py:23`)

```python
status_verified_date: str | None = Field(None, alias="statusVerifiedDate")
```

Parsing ensures it's a string, but doesn't validate the format (`YYYY-MM` or `YYYY-MM-DD`). Downstream code will need to handle any format.

### M6 — `flat_dict` silently drops fields with `None` values (`models.py:244`)

```python
return {k: v for k, v in d.items() if v is not None}
```

A field that is intentionally `None` (e.g. `has_results: None` for a record missing the field) is indistinguishable from a populated field. Consumer can't tell "no data" from "key absent." CSV/Parquet schemas will vary between records.

---

## Minor Issues

### m1 — `get_search_areas` returns generic `list[str]` (`client.py:139-141`)

The API returns a `dict` with a `"searchAreas"` key, not a bare list. This method will likely fail at runtime on a real response.

### m2 — No timeout on `time.sleep` in retry loop (`client.py:65,80,86`)

`time.sleep(2 ** attempt)` can sleep up to 8 seconds on the third retry. Consider a configurable max backoff cap.

### m3 — `_safe_flat` is `@staticmethod` but could be module-level (`parser.py:97`)

No `self` or `cls` dependency — should be a module-level function for testability.

### m4 — `StudyParser.stats()` reports `fetched_so_far` even when no fetch has been done (`parser.py:140-144`)

```python
def stats(self, **query_params: Any) -> dict[str, Any]:
    return {
        "total_count": self.client.get_total_count(**query_params),
        "fetched_so_far": len(self._fetched_ids),
    }
```

`fetched_so_far` is 0 until `parse_all` is called, so `stats()` on a fresh parser always returns 0 — mildly confusing.

### m5 — `if format == "duckdb" and table:` dead branch in CLI (`cli.py:109-112`)

```python
if format == "duckdb" and table:
    out_path = Path(output)
else:
    out_path = Path(output)
```

Both branches do the same thing. Remove the dead conditional.

### m6 — `duckdb` in both `pyproject.toml` extras and `requirements.txt` (`pyproject.toml:27, requirements.txt:6`)

`duckdb` is in `[project.optional-dependencies]` and also listed in `requirements.txt` (flat). If someone does `pip install -r requirements.txt` and doesn't have duckdb, install will fail. Either remove from `requirements.txt` or add a `--extra-index-url` note.

### m7 — `ruff` config missing (`pyproject.toml`)

No `[tool.ruff]` section. The project uses ruff for linting but doesn't pin its settings.

### m8 — `mypy` config missing (`pyproject.toml`)

No `[tool.mypy]` section. The project lists mypy as dev dependency but doesn't configure it.

### m9 — Integration tests use `pageSize=10` but no page token asserts (`test_client.py`)

The integration tests never check for `nextPageToken`, so multi-page iteration is untested against real API.

### m10 — No `pytest.ini` or `pyproject.toml [tool.pytest.ini_options]`

Test discovery, markers, and asyncio mode are not configured.

---

## Architecture Observations

### Good
- Clean separation: client / models / parser / storage / CLI
- RateLimiter is simple and testable
- ABC for writers makes adding new formats easy
- Resume-from-checkpoint is the right idea for a 593K-study fetch
- `flat_dict()` on the model avoids leaking flattening logic into the CLI

### Could Improve
- **Pagination abstraction leaks:** CLI constructs raw API query params (`query.cond`) — parser should own this translation
- **No schema enforcement across formats:** CSV header order == first-record key order; Parquet schema inferred from first N records (Pandas defaults). Schema drift across fetches is inevitable.
- **Memory-bound design:** Parquet and DuckDB writers accumulate all records before writing. For a dataset this large, streaming write is essential.
- **Error handling:** Pydantic validation failure → silent fallback to `_safe_flat`. A study with a structural issue gets silently truncated. Consider reporting errors explicitly.

### Security
- No credentials or secrets handled (good — API is public)
- No input sanitization needed (all input goes to API query params)
- No shell injection vectors

### Test Coverage Gaps
| Module | Lines | Tests | Coverage |
|--------|-------|-------|----------|
| client.py | 145 | 8 unit + 8 integration | ~70% (unit) |
| models.py | 250 | 4 | ~15% |
| parser.py | 144 | 0 | 0% |
| storage.py | 189 | 4 writers + 6 StorageWriter | ~50% |
| cli.py | 156 | 0 | 0% |

---

## Summary

**4 critical** (wrong type annotation, memory OOM on large fetches, no mid-fetch checkpoint, divergent failover paths),
**6 medium** (untested orchestrator, param collision, no-schema CSV, late duckdb import, dead code, dropping nulls),
**10 minor.**

The biggest risk in production: **OOM crash after fetching 200K+ studies** when using Parquet or DuckDB output since they buffer all records in memory.

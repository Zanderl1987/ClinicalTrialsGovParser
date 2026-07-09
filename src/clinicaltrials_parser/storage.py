from __future__ import annotations

import csv
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)


class BaseWriter(ABC):
    @abstractmethod
    def open(self, path: Path) -> None:
        ...

    @abstractmethod
    def write(self, record: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


class JsonLinesWriter(BaseWriter):
    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def open(self, path: Path | None = None) -> None:
        p = path or self.path
        self._fh = open(p, "w", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._fh:
            self._fh.close()


class JsonArrayWriter(BaseWriter):
    def __init__(self, path: Path):
        self.path = path
        self._fh = None
        self._first = True

    def open(self, path: Path | None = None) -> None:
        p = path or self.path
        self._fh = open(p, "w", encoding="utf-8")
        self._fh.write("[\n")
        self._first = True

    def write(self, record: dict[str, Any]) -> None:
        if not self._first:
            self._fh.write(",\n")
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False))
        self._first = False

    def close(self) -> None:
        if self._fh:
            self._fh.write("\n]\n")
            self._fh.close()


def _flatten_for_csv(record: dict[str, Any], prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for k, v in record.items():
        fk = f"{prefix}{k}"
        if isinstance(v, dict):
            result.update(_flatten_for_csv(v, prefix=f"{fk}_"))
        elif isinstance(v, list):
            result[fk] = "; ".join(str(x) for x in v) if v else ""
        elif v is None:
            result[fk] = ""
        else:
            result[fk] = str(v)
    return result


class CsvWriter(BaseWriter):
    def __init__(self, path: Path):
        self.path = path
        self._fh = None
        self._writer = None
        self._fieldnames: list[str] = []

    def open(self, path: Path | None = None) -> None:
        p = path or self.path
        self._fh = open(p, "w", encoding="utf-8", newline="")

    def write(self, record: dict[str, Any]) -> None:
        flat = _flatten_for_csv(record)
        if not self._fieldnames:
            self._fieldnames = list(flat.keys())
            self._writer = csv.DictWriter(self._fh, fieldnames=self._fieldnames)
            self._writer.writeheader()
        self._writer.writerow(flat)

    def close(self) -> None:
        if self._fh:
            self._fh.close()


class PolarsParquetWriter(BaseWriter):
    def __init__(self, path: Path, batch_size: int = 10000, compression: str = "snappy"):
        self.path = path
        self.batch_size = batch_size
        self.compression = compression
        self._records: list[dict[str, Any]] = []
        self._writer = None
        self._row_count = 0

    def open(self, path: Path | None = None) -> None:
        if path:
            self.path = path
        self._records = []
        self._writer = None
        self._row_count = 0

    def write(self, record: dict[str, Any]) -> None:
        self._records.append(record)
        if len(self._records) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        if not self._records:
            return
        import pyarrow.parquet as pq

        table = pl.DataFrame(self._records).to_arrow()

        if self._writer is None:
            self._writer = pq.ParquetWriter(
                self.path, table.schema, compression=self.compression
            )

        self._writer.write_table(table)
        self._row_count += len(self._records)
        self._records = []

    def close(self) -> None:
        self._flush()
        if self._writer is not None:
            self._writer.close()
            logger.info("Wrote %d rows to Parquet %s", self._row_count, self.path)
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq

            empty = pa.Table.from_pydict({})
            pq.write_table(empty, self.path, compression=self.compression)
            logger.warning("Wrote empty Parquet file to %s", self.path)


class DuckDbWriter(BaseWriter):
    def __init__(self, path: Path, table: str = "studies"):
        self.path = path
        self.table = table
        self._records: list[dict[str, Any]] = []
        self._batch_size = 10000
        self._table_created = False

    def open(self, path: Path | None = None) -> None:
        if path:
            self.path = path
        self._records = []
        self._table_created = False
        try:
            import duckdb  # noqa: F401
        except ImportError:
            raise ImportError(
                "duckdb is required for DuckDbWriter. "
                "Install with: pip install clinicaltrials-parser[duckdb]"
            )

    def write(self, record: dict[str, Any]) -> None:
        self._records.append(record)
        if len(self._records) >= self._batch_size:
            self._flush()

    def _flush(self) -> None:
        if not self._records:
            return
        import duckdb

        df = pl.DataFrame(self._records)
        con = duckdb.connect(str(self.path))
        con.register("_batch", df)
        if not self._table_created:
            con.execute(f"CREATE OR REPLACE TABLE {self.table} AS SELECT * FROM _batch")
            self._table_created = True
        else:
            con.execute(f"INSERT INTO {self.table} SELECT * FROM _batch")
        con.close()
        self._records = []

    def close(self) -> None:
        self._flush()
        if not self._table_created:
            import duckdb

            con = duckdb.connect(str(self.path))
            con.execute(f"CREATE TABLE {self.table} AS SELECT * FROM (SELECT NULL::INTEGER AS _empty LIMIT 0)")
            self._table_created = True
            con.close()
            logger.warning("Created empty DuckDB table %s.%s", self.path, self.table)


class IcebergWriter(BaseWriter):
    def __init__(
        self,
        path: Path,
        table: str = "studies",
        compression: str = "snappy",
        batch_size: int = 10000,
    ):
        self.path = path
        self.table_name = table
        self.compression = compression
        self.batch_size = batch_size
        self._records: list[dict[str, Any]] = []
        self._parquet_dir: Path | None = None
        self._parquet_writer: PolarsParquetWriter | None = None
        self._num_batches = 0

    def open(self, path: Path | None = None) -> None:
        if path:
            self.path = path
        self._records = []
        self._parquet_dir = self.path / ".iceberg_staging"
        self._parquet_dir.mkdir(parents=True, exist_ok=True)
        self._parquet_writer = None
        self._num_batches = 0

    def write(self, record: dict[str, Any]) -> None:
        self._records.append(record)
        if len(self._records) >= self.batch_size:
            self._flush()

    def _flush(self) -> None:
        if not self._records:
            return
        batch_id = f"{self._num_batches:05d}_{uuid.uuid4().hex[:8]}"
        parquet_path = self._parquet_dir / f"batch_{batch_id}.parquet"
        writer = PolarsParquetWriter(
            parquet_path,
            batch_size=self.batch_size,
            compression=self.compression,
        )
        writer.open()
        for r in self._records:
            writer.write(r)
        writer.close()
        self._num_batches += 1
        self._records = []

    def close(self) -> None:
        self._flush()
        if self._num_batches == 0:
            logger.warning("No data written to Iceberg table %s", self.table_name)
            return

        parquet_files = sorted(self._parquet_dir.glob("batch_*.parquet"))
        if not parquet_files:
            return

        import duckdb

        staging_dir = self.path / ".iceberg_staging"

        try:
            con = duckdb.connect()
            con.execute("INSTALL iceberg; LOAD iceberg;")

            file_list = ", ".join(f"'{p.as_posix()}'" for p in parquet_files)
            con.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet([{file_list}])
                ) TO '{self.path.as_posix()}' (
                    FORMAT ICEBERG,
                    COMPRESSION '{self.compression.upper()}'
                )
                """
            )
            logger.info(
                "Wrote Iceberg table to %s (%d batches, compression=%s)",
                self.path,
                self._num_batches,
                self.compression,
            )
        finally:
            con.close()
            self._cleanup_staging(staging_dir)

    @staticmethod
    def _cleanup_staging(staging_dir: Path) -> None:
        if staging_dir.is_dir():
            import shutil

            shutil.rmtree(staging_dir, ignore_errors=True)


_WRITERS: dict[str, type[BaseWriter]] = {
    "jsonl": JsonLinesWriter,
    "json": JsonArrayWriter,
    "csv": CsvWriter,
    "parquet": PolarsParquetWriter,
    "duckdb": DuckDbWriter,
    "iceberg": IcebergWriter,
}


@dataclass
class StorageWriter:
    fmt: str = "jsonl"
    output_path: Path | str | None = None
    table: str = "studies"
    batch_size: int = 10000
    compression: str = "snappy"
    _writer: BaseWriter | None = None

    def open(self, path: Path | None = None) -> None:
        p = Path(path or self.output_path)
        writer_cls = _WRITERS.get(self.fmt)
        if writer_cls is None:
            raise ValueError(f"Unknown format: {self.fmt}. Choose from: {', '.join(_WRITERS)}")

        kwargs: dict[str, Any] = {}
        if self.fmt == "duckdb":
            kwargs["table"] = self.table
        elif self.fmt == "iceberg":
            kwargs["table"] = self.table
            kwargs["compression"] = self.compression

        if self.fmt in ("parquet", "duckdb", "iceberg"):
            kwargs.setdefault("batch_size", self.batch_size)
        if self.fmt == "parquet":
            kwargs.setdefault("compression", self.compression)

        self._writer = writer_cls(p, **kwargs)
        self._writer.open()

    def write(self, record: dict[str, Any]) -> None:
        if self._writer is None:
            raise RuntimeError("StorageWriter not opened. Call .open() first.")
        self._writer.write(record)

    def close(self) -> None:
        if self._writer:
            self._writer.close()

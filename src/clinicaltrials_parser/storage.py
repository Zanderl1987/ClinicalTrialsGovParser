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


def _type_name(v: Any) -> str:
    if isinstance(v, list):
        if not v:
            return "list[empty]"
        elem_types = sorted({type(e).__name__ for e in v})
        return f"list[{','.join(elem_types)}]"
    if isinstance(v, dict):
        return "dict"
    if v is None:
        return "null"
    return type(v).__name__


class SchemaValidator:
    """Lightweight schema tracker: infers types from the first record
    and warns on type drift in subsequent records.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.fields: dict[str, str] | None = None
        self._drift_count = 0

    def validate(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if self.fields is None:
            self.fields = {k: _type_name(v) for k, v in record.items()}
            return

        for k, v in record.items():
            expected = self.fields.get(k)
            actual = _type_name(v)
            if expected is not None and expected != actual and actual != "null":
                self._drift_count += 1
                if self._drift_count <= 10:
                    logger.warning(
                        "Schema drift at field %r: expected %s, got %s",
                        k, expected, actual,
                    )

        missing = set(self.fields) - set(record)
        if missing:
            for k in missing:
                logger.debug("Record missing field: %s", k)

    def summary(self) -> str | None:
        if self.fields is None:
            return None
        cols = ", ".join(f"{k}:{t}" for k, t in self.fields.items())
        drifts = f", {self._drift_count} drift warnings" if self._drift_count else ""
        return f"Schema: [{cols}]{drifts}"


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


_DUCKDB_HAS_ICEBERG: bool | None = None


def _check_duckdb_iceberg() -> bool:
    """Probe whether DuckDB + iceberg extension supports COPY TO (FORMAT ICEBERG).
    Result is cached per process to avoid repeated probes.
    """
    global _DUCKDB_HAS_ICEBERG
    if _DUCKDB_HAS_ICEBERG is not None:
        return _DUCKDB_HAS_ICEBERG

    _DUCKDB_HAS_ICEBERG = False
    try:
        import duckdb
        import tempfile

        con = duckdb.connect()
        try:
            con.execute("INSTALL iceberg; LOAD iceberg;")
            con.execute("CREATE TABLE _ctgov_probe AS SELECT 1 AS x")
            with tempfile.TemporaryDirectory() as tmp:
                probe_dir = Path(tmp).as_posix()
                con.execute(
                    f"COPY _ctgov_probe TO '{probe_dir}' (FORMAT ICEBERG)"
                )
            _DUCKDB_HAS_ICEBERG = True
        except Exception:
            pass
        finally:
            try:
                con.execute("DROP TABLE IF EXISTS _ctgov_probe")
            except Exception:
                pass
            con.close()
    except ImportError:
        pass
    except Exception:
        pass

    logger.info("DuckDB Iceberg write support: %s", _DUCKDB_HAS_ICEBERG)
    return _DUCKDB_HAS_ICEBERG


class IcebergWriter(BaseWriter):
    def __init__(
        self,
        path: Path,
        table: str = "studies",
        compression: str = "snappy",
        batch_size: int = 10000,
        partition_by: str | None = None,
    ):
        self.path = path
        self.table_name = table
        self.compression = compression
        self.batch_size = batch_size
        self.partition_by = partition_by
        self._records: list[dict[str, Any]] = []
        self._parquet_dir: Path | None = None
        self._parquet_writer: PolarsParquetWriter | None = None
        self._num_batches = 0
        self._use_duckdb: bool | None = None

    def open(self, path: Path | None = None) -> None:
        if path:
            self.path = path
        self._records = []
        self._parquet_dir = self.path / ".iceberg_staging"
        self._parquet_dir.mkdir(parents=True, exist_ok=True)
        self._parquet_writer = None
        self._num_batches = 0

        self._use_duckdb = _check_duckdb_iceberg()

        if not self._use_duckdb:
            try:
                import pyiceberg  # noqa: F401
            except ImportError:
                raise ImportError(
                    "Neither DuckDB iceberg extension (requires DuckDB >= v1.5.3) "
                    "nor pyiceberg is available. "
                    "Install with: pip install clinicaltrials-parser[iceberg]"
                )

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

        try:
            if self._use_duckdb:
                self._finalize_duckdb(parquet_files)
            else:
                self._finalize_pyiceberg(parquet_files)
        finally:
            self._cleanup_staging(self._parquet_dir)

    def _finalize_duckdb(self, parquet_files: list[Path]) -> None:
        import duckdb

        con = duckdb.connect()
        try:
            con.execute("INSTALL iceberg; LOAD iceberg;")
            file_list = ", ".join(f"'{p.as_posix()}'" for p in parquet_files)
            partition_clause = (
                f", PARTITION_BY ({self.partition_by})" if self.partition_by else ""
            )
            con.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet([{file_list}])
                ) TO '{self.path.as_posix()}' (
                    FORMAT ICEBERG,
                    COMPRESSION '{self.compression.upper()}'
                    {partition_clause}
                )
                """
            )
            logger.info(
                "Wrote Iceberg table to %s (%d batches, engine=duckdb, compression=%s, partition_by=%s)",
                self.path,
                self._num_batches,
                self.compression,
                self.partition_by,
            )
        finally:
            con.close()

    def _finalize_pyiceberg(self, parquet_files: list[Path]) -> None:
        from pyiceberg.catalog.sql import SqlCatalog
        from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
        from pyiceberg.partitioning import PartitionField, PartitionSpec
        from pyiceberg.transforms import IdentityTransform
        import pyarrow.parquet as pq

        catalog_db = self.path / ".iceberg_catalog.db"
        catalog = SqlCatalog(
            "default",
            **{
                "uri": f"sqlite:///{catalog_db.as_posix()}",
                "warehouse": str(self.path.parent),
                "io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
            },
        )

        try:
            catalog.create_namespace(("ctgov",))
        except NamespaceAlreadyExistsError:
            pass

        schema = pq.read_schema(parquet_files[0])

        try:
            catalog.drop_table(("ctgov", self.table_name))
        except NoSuchTableError:
            pass

        partition_spec = None
        if self.partition_by:
            field_names = [f.name for f in schema]
            if self.partition_by not in field_names:
                logger.warning(
                    "Partition field %r not in schema (%s), ignoring",
                    self.partition_by,
                    field_names,
                )
            else:
                source_id = field_names.index(self.partition_by)
                partition_spec = PartitionSpec(
                    PartitionField(
                        source_id=source_id,
                        field_id=1000,
                        transform=IdentityTransform(),
                        name=self.partition_by,
                    )
                )

        table = catalog.create_table(
            ("ctgov", self.table_name),
            schema,
            partition_spec=partition_spec,
            location=str(self.path),
        )

        for pf in parquet_files:
            tbl = pq.read_table(pf)
            table.append(tbl)

        logger.info(
            "Wrote Iceberg table to %s (%d batches, engine=pyiceberg, compression=%s, partition_by=%s)",
            self.path,
            self._num_batches,
            self.compression,
            self.partition_by,
        )

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
    validate_schema: bool = True
    partition_by: str | None = None
    _writer: BaseWriter | None = None
    _schema: SchemaValidator | None = None

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
            kwargs["partition_by"] = self.partition_by

        if self.fmt in ("parquet", "duckdb", "iceberg"):
            kwargs.setdefault("batch_size", self.batch_size)
        if self.fmt == "parquet":
            kwargs.setdefault("compression", self.compression)

        self._writer = writer_cls(p, **kwargs)
        self._writer.open()
        self._schema = SchemaValidator(enabled=self.validate_schema)

    def write(self, record: dict[str, Any]) -> None:
        if self._writer is None:
            raise RuntimeError("StorageWriter not opened. Call .open() first.")
        if self._schema:
            self._schema.validate(record)
        self._writer.write(record)

    def close(self) -> None:
        if self._writer:
            self._writer.close()
        if self._schema and self._schema.fields:
            s = self._schema.summary()
            if s:
                logger.info(s)

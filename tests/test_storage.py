from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from clinicaltrials_parser.storage import (
    CsvWriter,
    IcebergWriter,
    JsonArrayWriter,
    JsonLinesWriter,
    PolarsParquetWriter,
    StorageWriter,
)


SAMPLE_RECORDS = [
    {"nct_id": "NCT001", "status": "COMPLETED", "conditions": ["Cancer"]},
    {"nct_id": "NCT002", "status": "RECRUITING", "conditions": ["Diabetes", "Obesity"]},
]


class TestJsonLinesWriter:
    def test_write(self, tmp_path: Path):
        p = tmp_path / "test.jsonl"
        w = JsonLinesWriter(p)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["nct_id"] == "NCT001"


class TestJsonArrayWriter:
    def test_write(self, tmp_path: Path):
        p = tmp_path / "test.json"
        w = JsonArrayWriter(p)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        data = json.loads(p.read_text())
        assert len(data) == 2
        assert data[0]["nct_id"] == "NCT001"


class TestCsvWriter:
    def test_write(self, tmp_path: Path):
        p = tmp_path / "test.csv"
        w = CsvWriter(p)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        df = pl.read_csv(p)
        assert len(df) == 2
        assert df["nct_id"][0] == "NCT001"
        assert "Diabetes; Obesity" in df["conditions"][1]


class TestPolarsParquetWriter:
    def test_write(self, tmp_path: Path):
        p = tmp_path / "test.parquet"
        w = PolarsParquetWriter(p, batch_size=10)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        df = pl.read_parquet(p)
        assert len(df) == 2
        assert df["nct_id"][0] == "NCT001"

    def test_batch_flush(self, tmp_path: Path):
        p = tmp_path / "batch.parquet"
        w = PolarsParquetWriter(p, batch_size=1)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        df = pl.read_parquet(p)
        assert len(df) == 2

    def test_empty_write(self, tmp_path: Path):
        p = tmp_path / "empty.parquet"
        w = PolarsParquetWriter(p)
        w.open()
        w.close()

        df = pl.read_parquet(p)
        assert len(df) == 0

    def test_compression_option(self, tmp_path: Path):
        p = tmp_path / "compressed.parquet"
        w = PolarsParquetWriter(p, compression="zstd")
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        df = pl.read_parquet(p)
        assert len(df) == 2


class TestStorageWriter:
    def test_jsonl_format(self, tmp_path: Path):
        p = tmp_path / "out.jsonl"
        sw = StorageWriter(fmt="jsonl")
        sw.output_path = p
        sw.open()
        for r in SAMPLE_RECORDS:
            sw.write(r)
        sw.close()
        assert p.exists()

    def test_csv_format(self, tmp_path: Path):
        p = tmp_path / "out.csv"
        sw = StorageWriter(fmt="csv")
        sw.output_path = p
        sw.open()
        for r in SAMPLE_RECORDS:
            sw.write(r)
        sw.close()
        assert p.exists()

    def test_parquet_format(self, tmp_path: Path):
        p = tmp_path / "out.parquet"
        sw = StorageWriter(fmt="parquet")
        sw.output_path = p
        sw.open()
        for r in SAMPLE_RECORDS:
            sw.write(r)
        sw.close()
        assert p.exists()
        df = pl.read_parquet(p)
        assert len(df) == 2

    def test_invalid_format(self):
        sw = StorageWriter(fmt="invalid")
        with pytest.raises(ValueError):
            sw.open("test.txt")

    def test_write_without_open(self):
        sw = StorageWriter(fmt="jsonl")
        with pytest.raises(RuntimeError):
            sw.write({"nct_id": "NCT001"})

    def test_parquet_compression(self, tmp_path: Path):
        p = tmp_path / "compressed.parquet"
        sw = StorageWriter(fmt="parquet", compression="zstd")
        sw.output_path = p
        sw.open()
        for r in SAMPLE_RECORDS:
            sw.write(r)
        sw.close()
        assert p.stat().st_size > 0


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("duckdb"),
    reason="duckdb not installed (pip install clinicaltrials-parser[duckdb])",
)
class TestIcebergWriter:
    def test_write(self, tmp_path: Path):
        out_dir = tmp_path / "iceberg_out"
        w = IcebergWriter(out_dir, table="test_studies")
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        data_dir = out_dir / "data"
        metadata_dir = out_dir / "metadata"
        assert data_dir.is_dir()
        assert metadata_dir.is_dir()
        assert list(data_dir.glob("*.parquet"))
        assert list(metadata_dir.glob("*.metadata.json"))

        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        result = con.execute(
            f"SELECT nct_id, status FROM iceberg_scan('{out_dir.as_posix()}') ORDER BY nct_id"
        ).fetchall()
        assert result == [("NCT001", "COMPLETED"), ("NCT002", "RECRUITING")]

    def test_batch_flush(self, tmp_path: Path):
        out_dir = tmp_path / "iceberg_batch"
        w = IcebergWriter(out_dir, table="batch_test", batch_size=1)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        assert (out_dir / "data").is_dir()

        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        result = con.execute(
            f"SELECT nct_id FROM iceberg_scan('{out_dir.as_posix()}') ORDER BY nct_id"
        ).fetchall()
        assert result == [("NCT001",), ("NCT002",)]

    def test_snappy_compression(self, tmp_path: Path):
        out_dir = tmp_path / "iceberg_snappy"
        w = IcebergWriter(out_dir, compression="snappy")
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        result = con.execute(
            f"SELECT nct_id FROM iceberg_scan('{out_dir.as_posix()}') ORDER BY nct_id"
        ).fetchall()
        assert result == [("NCT001",), ("NCT002",)]

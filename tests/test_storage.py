from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from clinicaltrials_parser.storage import (
    CsvWriter,
    DuckDbWriter,
    IcebergWriter,
    JsonArrayWriter,
    JsonLinesWriter,
    PolarsParquetWriter,
    StorageWriter,
    _flatten_for_csv,
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

    def test_append_reuses_existing_header_and_data(self, tmp_path: Path):
        p = tmp_path / "resume.csv"
        w1 = CsvWriter(p)
        w1.open()
        w1.write(SAMPLE_RECORDS[0])
        w1.close()

        w2 = CsvWriter(p, append=True)
        w2.open()
        w2.write(SAMPLE_RECORDS[1])
        w2.close()

        df = pl.read_csv(p)
        assert len(df) == 2
        assert set(df["nct_id"]) == {"NCT001", "NCT002"}
        # header must not be duplicated
        assert p.read_text().count("nct_id") == 1


class TestFlattenForCsv:
    def test_list_of_dicts_serialized_as_json_not_python_repr(self):
        record = {
            "overall_officials": [
                {"name": "Dr. Jane Smith", "role": "PI"},
                {"name": "Dr. Bob Lee", "role": "Sub-I"},
            ]
        }
        flat = _flatten_for_csv(record)
        cells = flat["overall_officials"].split("; ")
        assert len(cells) == 2
        for cell in cells:
            parsed = json.loads(cell)
            assert "name" in parsed and "role" in parsed


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


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("duckdb"),
    reason="duckdb not installed (pip install clinicaltrials-parser[duckdb])",
)
class TestDuckDbWriter:
    def test_direct_construction_accepts_batch_size(self, tmp_path: Path):
        # Regression: StorageWriter.open() always passes batch_size for
        # fmt="duckdb"; DuckDbWriter must accept it or every duckdb fetch
        # crashes with TypeError before writing anything.
        p = tmp_path / "direct.duckdb"
        w = DuckDbWriter(p, table="studies", batch_size=5)
        w.open()
        for r in SAMPLE_RECORDS:
            w.write(r)
        w.close()

        import duckdb

        con = duckdb.connect(str(p))
        rows = con.execute("SELECT nct_id FROM studies ORDER BY nct_id").fetchall()
        con.close()
        assert rows == [("NCT001",), ("NCT002",)]

    def test_rejects_unsafe_table_name(self, tmp_path: Path):
        p = tmp_path / "injection.duckdb"
        with pytest.raises(ValueError):
            DuckDbWriter(p, table="studies; DROP TABLE studies; --")

    def test_rejects_sql_injection_via_table_name(self, tmp_path: Path):
        p = tmp_path / "leak.duckdb"
        malicious = "leak AS SELECT * FROM read_csv_auto('nonexistent.csv') --"
        with pytest.raises(ValueError):
            DuckDbWriter(p, table=malicious)

    def test_append_inserts_into_existing_table(self, tmp_path: Path):
        p = tmp_path / "resume.duckdb"
        w1 = DuckDbWriter(p, table="studies")
        w1.open()
        w1.write(SAMPLE_RECORDS[0])
        w1.close()

        w2 = DuckDbWriter(p, table="studies", append=True)
        w2.open()
        w2.write(SAMPLE_RECORDS[1])
        w2.close()

        import duckdb

        con = duckdb.connect(str(p))
        rows = con.execute("SELECT nct_id FROM studies ORDER BY nct_id").fetchall()
        con.close()
        assert rows == [("NCT001",), ("NCT002",)]


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

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("duckdb"),
        reason="duckdb not installed (pip install clinicaltrials-parser[duckdb])",
    )
    def test_duckdb_format(self, tmp_path: Path):
        # Regression: StorageWriter(fmt="duckdb").open() used to raise
        # TypeError unconditionally (DuckDbWriter had no batch_size param).
        p = tmp_path / "out.duckdb"
        sw = StorageWriter(fmt="duckdb", batch_size=10000)
        sw.output_path = p
        sw.open()
        for r in SAMPLE_RECORDS:
            sw.write(r)
        sw.close()
        assert p.exists()

        import duckdb

        con = duckdb.connect(str(p))
        rows = con.execute("SELECT nct_id FROM studies ORDER BY nct_id").fetchall()
        con.close()
        assert rows == [("NCT001",), ("NCT002",)]

    def test_resume_appends_for_jsonl(self, tmp_path: Path):
        p = tmp_path / "resume.jsonl"
        sw1 = StorageWriter(fmt="jsonl", output_path=p)
        sw1.open()
        sw1.write(SAMPLE_RECORDS[0])
        sw1.close()

        sw2 = StorageWriter(fmt="jsonl", output_path=p)
        sw2.open(resume=True)
        sw2.write(SAMPLE_RECORDS[1])
        sw2.close()

        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        nct_ids = {json.loads(line)["nct_id"] for line in lines}
        assert nct_ids == {"NCT001", "NCT002"}

    def test_resume_rejects_parquet_with_existing_output(self, tmp_path: Path):
        p = tmp_path / "resume.parquet"
        sw1 = StorageWriter(fmt="parquet", output_path=p)
        sw1.open()
        sw1.write(SAMPLE_RECORDS[0])
        sw1.close()

        sw2 = StorageWriter(fmt="parquet", output_path=p)
        with pytest.raises(ValueError, match="not supported for format=parquet"):
            sw2.open(resume=True)

    def test_resume_rejects_json_array_with_existing_output(self, tmp_path: Path):
        p = tmp_path / "resume.json"
        sw1 = StorageWriter(fmt="json", output_path=p)
        sw1.open()
        sw1.write(SAMPLE_RECORDS[0])
        sw1.close()

        sw2 = StorageWriter(fmt="json", output_path=p)
        with pytest.raises(ValueError, match="not supported for format=json"):
            sw2.open(resume=True)

    def test_resume_with_no_existing_output_does_not_raise(self, tmp_path: Path):
        # Fresh --resume run (resume file present but nothing written yet)
        # against a non-existent output path must not be blocked.
        p = tmp_path / "fresh.parquet"
        sw = StorageWriter(fmt="parquet", output_path=p)
        sw.open(resume=True)
        sw.write(SAMPLE_RECORDS[0])
        sw.close()
        assert p.exists()

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

    def test_rejects_unsafe_table_name(self, tmp_path: Path):
        with pytest.raises(ValueError):
            IcebergWriter(tmp_path / "out", table="studies; DROP TABLE studies; --")

    def test_rejects_unsafe_partition_by(self, tmp_path: Path):
        with pytest.raises(ValueError):
            IcebergWriter(tmp_path / "out", partition_by="status) --")

    def test_rejects_invalid_compression(self, tmp_path: Path):
        with pytest.raises(ValueError):
            IcebergWriter(tmp_path / "out", compression="' OR 1=1 --")

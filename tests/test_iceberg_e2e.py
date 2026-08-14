from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clinicaltrials_parser.parser import StudyParser
from clinicaltrials_parser.storage import StorageWriter

from .test_models import RAW_STUDY

RAW_STUDY_2 = {
    **RAW_STUDY,
    "protocolSection": {
        **RAW_STUDY["protocolSection"],
        "identificationModule": {
            **RAW_STUDY["protocolSection"]["identificationModule"],
            "nctId": "NCT04000010",
            "briefTitle": "Second Test Study",
        },
        "statusModule": {
            **RAW_STUDY["protocolSection"]["statusModule"],
            "overallStatus": "COMPLETED",
        },
    },
}


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("duckdb"),
    reason="duckdb not installed (pip install clinicaltrials-parser[duckdb])",
)
class TestIcebergEndToEnd:
    def test_parser_to_iceberg(self, tmp_path):
        mock_client = MagicMock()
        mock_client.iter_studies.return_value = [RAW_STUDY, RAW_STUDY_2]

        storage = StorageWriter(fmt="iceberg", table="studies", batch_size=1)
        parser = StudyParser(client=mock_client, storage=storage)

        out_dir = tmp_path / "warehouse"
        parser.to_storage(output_path=out_dir, fmt="iceberg")

        data_dir = out_dir / "data"
        metadata_dir = out_dir / "metadata"
        assert data_dir.is_dir()
        assert metadata_dir.is_dir()
        assert list(data_dir.glob("*.parquet"))

        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        result = con.execute(
            f"SELECT nct_id, overall_status FROM iceberg_scan('{out_dir.as_posix()}') ORDER BY nct_id"
        ).fetchall()
        assert len(result) == 2
        assert result[0] == ("NCT04000009", "TERMINATED")
        assert result[1] == ("NCT04000010", "COMPLETED")

    def test_partitioned_by_status(self, tmp_path):
        mock_client = MagicMock()
        mock_client.iter_studies.return_value = [RAW_STUDY, RAW_STUDY_2]

        storage = StorageWriter(fmt="iceberg", table="studies", batch_size=1, partition_by="overall_status")
        parser = StudyParser(client=mock_client, storage=storage)

        out_dir = tmp_path / "warehouse_partitioned"
        parser.to_storage(output_path=out_dir, fmt="iceberg")

        assert (out_dir / "data").is_dir()
        partition_dirs = list((out_dir / "data").iterdir())
        assert len(partition_dirs) >= 1

        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL iceberg; LOAD iceberg;")
        result = con.execute(
            f"SELECT nct_id, overall_status FROM iceberg_scan('{out_dir.as_posix()}') ORDER BY nct_id"
        ).fetchall()
        assert len(result) == 2
        assert result[0] == ("NCT04000009", "TERMINATED")
        assert result[1] == ("NCT04000010", "COMPLETED")

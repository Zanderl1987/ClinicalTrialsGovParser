from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clinicaltrials_parser.parser import StudyParser
from clinicaltrials_parser.storage import StorageWriter


def _make_raw_study(nct_id: str) -> dict:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id, "briefTitle": f"Study {nct_id}"},
            "statusModule": {"overallStatus": "RECRUITING"},
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "X"}]},
            "designModule": {"enrollmentInfo": {"count": 42}},
        },
        "hasResults": False,
    }


def _mock_client(n: int = 5):
    client = MagicMock()
    client.iter_studies.return_value = iter(_make_raw_study(f"NCT{i:03d}") for i in range(n))
    return client


class TestParseAllMaxStudies:
    def test_max_studies_zero_yields_nothing(self):
        parser = StudyParser(client=_mock_client(5))
        results = list(parser.parse_all(max_studies=0))
        assert results == []

    def test_max_studies_positive_truncates(self):
        parser = StudyParser(client=_mock_client(5))
        results = list(parser.parse_all(max_studies=2))
        assert len(results) == 2

    def test_max_studies_none_fetches_all(self):
        parser = StudyParser(client=_mock_client(5))
        results = list(parser.parse_all(max_studies=None))
        assert len(results) == 5


class TestSafeFlat:
    def test_includes_intervention_types_and_enrollment_count(self):
        raw = _make_raw_study("NCT001")
        flat = StudyParser._safe_flat(raw)
        assert flat["intervention_types"] == ["DRUG"]
        assert flat["enrollment_count"] == 42


class TestResumeState:
    def test_is_resuming_false_without_resume_file(self):
        parser = StudyParser(client=_mock_client(0))
        assert parser.is_resuming is False

    def test_is_resuming_true_after_loading_nonempty_state(self, tmp_path: Path):
        resume_file = tmp_path / "resume.json"
        resume_file.write_text(json.dumps({"fetched_ids": ["NCT001"]}))
        parser = StudyParser(client=_mock_client(0), resume_file=str(resume_file))
        assert parser.is_resuming is True

    def test_resume_state_saved_on_completion(self, tmp_path: Path):
        resume_file = tmp_path / "resume.json"
        parser = StudyParser(client=_mock_client(3), resume_file=str(resume_file))
        list(parser.parse_all())
        data = json.loads(resume_file.read_text())
        assert set(data["fetched_ids"]) == {"NCT000", "NCT001", "NCT002"}

    def test_resume_state_saved_even_if_fetch_raises(self, tmp_path: Path):
        resume_file = tmp_path / "resume.json"
        client = MagicMock()

        def _raising_iter():
            yield _make_raw_study("NCT000")
            yield _make_raw_study("NCT001")
            raise RuntimeError("simulated network failure")

        client.iter_studies.return_value = _raising_iter()
        parser = StudyParser(client=client, resume_file=str(resume_file))

        with pytest.raises(RuntimeError):
            list(parser.parse_all())

        data = json.loads(resume_file.read_text())
        assert set(data["fetched_ids"]) == {"NCT000", "NCT001"}

    def test_already_fetched_ids_are_skipped(self, tmp_path: Path):
        resume_file = tmp_path / "resume.json"
        resume_file.write_text(json.dumps({"fetched_ids": ["NCT000", "NCT001"]}))
        parser = StudyParser(client=_mock_client(3), resume_file=str(resume_file))
        results = list(parser.parse_all())
        nct_ids = {r["nct_id"] for r in results}
        assert nct_ids == {"NCT002"}


class TestToStorageClosesOnException:
    def test_storage_closed_even_if_write_raises(self, tmp_path: Path):
        p = tmp_path / "out.jsonl"
        storage = StorageWriter(fmt="jsonl")
        close_calls = []
        original_close = storage.close
        storage.close = lambda: (close_calls.append(True), original_close())[1]

        client = MagicMock()

        def _raising_iter():
            yield _make_raw_study("NCT000")
            raise RuntimeError("simulated failure mid-fetch")

        client.iter_studies.return_value = _raising_iter()
        parser = StudyParser(client=client, storage=storage)

        with pytest.raises(RuntimeError):
            parser.to_storage(output_path=p, fmt="jsonl")

        assert close_calls, "storage.close() must run even when the fetch loop raises"

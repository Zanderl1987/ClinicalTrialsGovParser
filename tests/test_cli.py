from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clinicaltrials_parser.cli import main


class TestStatsStatusParsing:
    def test_trailing_comma_does_not_send_empty_status(self):
        # Regression: "RECRUITING," used to split into ["RECRUITING", ""],
        # sending an empty-string filter value to the API.
        runner = CliRunner()
        with patch(
            "clinicaltrials_parser.cli.ClinicalTrialsClient.get_total_count",
            return_value=42,
        ) as mock_count:
            result = runner.invoke(main, ["stats", "--status", "RECRUITING,"])

        assert result.exit_code == 0
        called_params = mock_count.call_args.kwargs
        assert called_params["filter.overallStatus"] == ["RECRUITING"]

    def test_multiple_statuses_still_all_included(self):
        runner = CliRunner()
        with patch(
            "clinicaltrials_parser.cli.ClinicalTrialsClient.get_total_count",
            return_value=42,
        ) as mock_count:
            result = runner.invoke(
                main, ["stats", "--status", "RECRUITING,ACTIVE_NOT_RECRUITING"]
            )

        assert result.exit_code == 0
        called_params = mock_count.call_args.kwargs
        assert called_params["filter.overallStatus"] == ["RECRUITING", "ACTIVE_NOT_RECRUITING"]


class TestPhaseAndStudyTypeFilters:
    # The API has no filter.phase / filter.studyType parameters (400); they must be
    # sent as a filter.advanced AREA[] expression.
    def test_stats_study_type_uses_advanced_filter(self):
        runner = CliRunner()
        with patch(
            "clinicaltrials_parser.cli.ClinicalTrialsClient.get_total_count",
            return_value=42,
        ) as mock_count:
            result = runner.invoke(main, ["stats", "--study-type", "INTERVENTIONAL"])

        assert result.exit_code == 0
        assert mock_count.call_args.kwargs == {"filter.advanced": "AREA[StudyType]INTERVENTIONAL"}

    def test_phase_and_study_type_combine(self):
        from clinicaltrials_parser.cli import _advanced_filter

        assert _advanced_filter("PHASE2, PHASE3,", "INTERVENTIONAL") == (
            "AREA[Phase](PHASE2 OR PHASE3) AND AREA[StudyType]INTERVENTIONAL"
        )
        assert _advanced_filter(None, None) is None


@pytest.mark.skipif(
    not os.environ.get("CTGOV_INTEGRATION_TESTS"),
    reason="Set CTGOV_INTEGRATION_TESTS=1 to run integration tests",
)
def test_fetch_end_to_end_live(tmp_path):
    # The real CLI path (User-Agent, None params, filter syntax) against the live API.
    out = tmp_path / "p3.jsonl"
    result = CliRunner().invoke(main, [
        "fetch", "--phase", "PHASE3", "--study-type", "INTERVENTIONAL",
        "--max-studies", "3", "--no-progress", "--no-flat", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    for r in rows:
        design = r["protocolSection"]["designModule"]
        assert design["studyType"] == "INTERVENTIONAL"
        assert "PHASE3" in design["phases"]

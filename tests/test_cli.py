from __future__ import annotations

from unittest.mock import patch

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

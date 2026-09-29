from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clinicaltrials_parser.client import ClinicalTrialsClient, RateLimiter

SAMPLE_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT04000009",
            "briefTitle": "Test Study",
            "officialTitle": "A Test Study",
        },
        "statusModule": {"overallStatus": "COMPLETED"},
        "conditionsModule": {"conditions": ["Condition A"]},
        "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE3"]},
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "Test Sponsor", "class": "INDUSTRY"}
        },
    },
    "hasResults": True,
}

SAMPLE_PAGE = {"studies": [SAMPLE_STUDY], "nextPageToken": None}


class TestRateLimiter:
    def test_basic(self):
        rl = RateLimiter(1000)
        import time

        t0 = time.monotonic()
        rl.wait()
        rl.wait()
        rl.wait()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1

    def test_zero_rate_limit_raises_clean_error(self):
        with pytest.raises(ValueError):
            RateLimiter(0)

    def test_negative_rate_limit_raises_clean_error(self):
        with pytest.raises(ValueError):
            RateLimiter(-5)

    def test_zero_rate_limit_client_construction_raises(self):
        with pytest.raises(ValueError):
            ClinicalTrialsClient(rate_limit=0)


def _mock_response(status_code=200, json_data=None):
    m = MagicMock(spec=httpx.Response)
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.headers = {}
    return m


class TestClinicalTrialsClientUnit:
    def test_get_total_count(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data={"totalCount": 593126})
            count = client.get_total_count(**{"filter.overallStatus": "TERMINATED"})
            assert count == 593126
            path, params = mock_request.call_args.args[1], mock_request.call_args.kwargs["params"]
            assert path == "/studies"
            assert params["countTotal"] == "true"
            assert params["filter.overallStatus"] == "TERMINATED"

    def test_get_study(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data=SAMPLE_STUDY)
            study = client.get_study("NCT04000009")
            assert study["protocolSection"]["identificationModule"]["nctId"] == "NCT04000009"

    def test_get_studies_page(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data=SAMPLE_PAGE)
            studies, next_token = client.get_studies_page(pageSize=10)
            assert len(studies) == 1
            assert next_token is None

    def test_get_studies_page_drops_none_params(self):
        # Regression: fields=None was sent as an empty "fields=" and the API answered 400.
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data=SAMPLE_PAGE)
            client.get_studies_page(fields=None)
            assert "fields" not in mock_request.call_args.kwargs["params"]

    def test_iter_studies(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "get_studies_page") as mock_method:
            mock_method.side_effect = [
                ([SAMPLE_STUDY], "token2"),
                ([SAMPLE_STUDY], None),
            ]
            results = list(client.iter_studies(pageSize=10))
            assert len(results) == 2

    def test_fetch_all_studies(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "iter_studies") as mock_iter:
            mock_iter.return_value = iter([SAMPLE_STUDY, SAMPLE_STUDY])
            studies = client.fetch_all_studies(pageSize=10)
            assert len(studies) == 2

    def test_get_stats(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(
                json_data={"totalStudies": 593126, "averageSizeBytes": 17259}
            )
            stats = client.get_stats()
            assert stats["totalStudies"] == 593126

    def test_get_field_metadata(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data={"fields": []})
            metadata = client.get_field_metadata()
            assert metadata == {"fields": []}

    def test_get_enums(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(
                json_data={"overallStatus": ["RECRUITING", "COMPLETED"]}
            )
            enums = client.get_enums()
            assert enums == {"overallStatus": ["RECRUITING", "COMPLETED"]}

    def test_get_search_areas(self):
        client = ClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data=["Condition", "Intervention"])
            areas = client.get_search_areas()
            assert areas == ["Condition", "Intervention"]

    def test_rate_limiting_respected(self):
        client = ClinicalTrialsClient(page_size=10, rate_limit=100)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data=SAMPLE_PAGE)
            for study in client.iter_studies(pageSize=10):
                break


class TestAsyncClinicalTrialsClient:
    def test_zero_rate_limit_raises_clean_error(self):
        from clinicaltrials_parser.client import AsyncRateLimiter

        with pytest.raises(ValueError):
            AsyncRateLimiter(0)

    @pytest.mark.asyncio
    async def test_get_total_count(self):
        from clinicaltrials_parser.client import AsyncClinicalTrialsClient

        client = AsyncClinicalTrialsClient(page_size=10)
        with patch.object(client, "_request") as mock_request:
            mock_request.return_value = _mock_response(json_data={"totalCount": 593126})
            count = await client.get_total_count()
            assert count == 593126

    @pytest.mark.asyncio
    async def test_iter_studies(self):
        from clinicaltrials_parser.client import AsyncClinicalTrialsClient

        client = AsyncClinicalTrialsClient(page_size=10)
        with patch.object(client, "get_studies_page") as mock_method:
            mock_method.side_effect = [
                ([SAMPLE_STUDY], "token2"),
                ([SAMPLE_STUDY], None),
            ]
            results = []
            async for study in client.iter_studies(pageSize=10):
                results.append(study)
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_fetch_all_studies(self):
        from clinicaltrials_parser.client import AsyncClinicalTrialsClient

        client = AsyncClinicalTrialsClient(page_size=10)
        with patch.object(client, "get_studies_page") as mock_method:
            mock_method.side_effect = [
                ([SAMPLE_STUDY], None),
            ]
            results = await client.fetch_all_studies(pageSize=10)
            assert len(results) == 1


run_integration = pytest.mark.skipif(
    not os.environ.get("CTGOV_INTEGRATION_TESTS"),
    reason="Set CTGOV_INTEGRATION_TESTS=1 to run integration tests (requires network access to clinicaltrials.gov)",
)


@run_integration
class TestClinicalTrialsClientIntegration:
    def test_get_total_count(self):
        client = ClinicalTrialsClient(page_size=10)
        count = client.get_total_count()
        assert isinstance(count, int)
        assert count > 500000

    def test_get_study(self):
        client = ClinicalTrialsClient(page_size=10)
        study = client.get_study("NCT04000009")
        assert study["protocolSection"]["identificationModule"]["nctId"] == "NCT04000009"

    def test_get_studies_page(self):
        client = ClinicalTrialsClient(page_size=10)
        studies, next_token = client.get_studies_page(pageSize=10)
        assert len(studies) == 10
        assert "protocolSection" in studies[0]

    def test_iter_studies(self):
        client = ClinicalTrialsClient(page_size=10)
        count = 0
        for study in client.iter_studies(pageSize=10):
            assert "protocolSection" in study
            count += 1
            if count >= 25:
                break
        assert count == 25

    def test_fetch_all_studies(self):
        # fetch_all_studies pages to the end, so it must be filtered; unfiltered it
        # downloads the whole registry.
        client = ClinicalTrialsClient(page_size=100)
        studies = client.fetch_all_studies(**{"filter.ids": "NCT04000009,NCT00000102"})
        assert len(studies) == 2

    def test_get_total_count_filtered(self):
        client = ClinicalTrialsClient(page_size=10)
        assert client.get_total_count(**{"filter.ids": "NCT04000009,NCT00000102"}) == 2

    def test_get_stats(self):
        client = ClinicalTrialsClient(page_size=10)
        stats = client.get_stats()
        assert "totalStudies" in stats
        assert stats["totalStudies"] > 500000

    def test_get_field_metadata(self):
        client = ClinicalTrialsClient(page_size=10)
        metadata = client.get_field_metadata()
        assert isinstance(metadata, list) and metadata

    def test_get_enums(self):
        client = ClinicalTrialsClient(page_size=10)
        enums = client.get_enums()
        assert isinstance(enums, list) and enums

    def test_get_search_areas(self):
        client = ClinicalTrialsClient(page_size=10)
        areas = client.get_search_areas()
        assert isinstance(areas, list)

    def test_fields_param(self):
        client = ClinicalTrialsClient(page_size=10)
        study = client.get_study(
            "NCT04000009",
            fields="protocolSection.identificationModule.nctId,protocolSection.statusModule.overallStatus",
        )
        assert "protocolSection" in study
        assert "identificationModule" in study["protocolSection"]
        assert "briefTitle" not in study["protocolSection"]["identificationModule"]

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("psycopg2"),
    reason="psycopg2 not installed (pip install clinicaltrials-parser[aact])",
)
class TestAactClient:
    def test_iter_studies(self):
        from clinicaltrials_parser.aact import AactClient

        client = AactClient(page_size=10)

        mock_conn = MagicMock()

        def make_cursor(fetch_result):
            c = MagicMock()
            c.fetchall.return_value = fetch_result
            return c

        page_cursor = make_cursor(
            [
                (
                    "NCT04000009",
                    "Test Study",
                    "Official Title",
                    "RECRUITING",
                    "INTERVENTIONAL",
                    "PHASE3",
                    100,
                    "ACTUAL",
                    None,
                    None,
                    None,
                    None,
                    None,
                    True,
                    "Test Sponsor",
                    "INDUSTRY",
                )
            ]
        )
        empty_cursor = make_cursor([])
        mock_conn.cursor.side_effect = [page_cursor, empty_cursor, empty_cursor]

        with patch.object(client, "_get_conn", return_value=mock_conn):
            results = list(client.iter_studies(max_studies=5))

        assert len(results) == 1
        assert results[0]["protocolSection"]["identificationModule"]["nctId"] == "NCT04000009"
        assert results[0]["protocolSection"]["statusModule"]["overallStatus"] == "RECRUITING"
        assert results[0]["hasResults"] is True

    def test_get_total_count(self):
        from clinicaltrials_parser.aact import AactClient

        client = AactClient()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (593126,)

        with patch.object(client, "_get_conn", return_value=mock_conn):
            count = client.get_total_count()

        assert count == 593126

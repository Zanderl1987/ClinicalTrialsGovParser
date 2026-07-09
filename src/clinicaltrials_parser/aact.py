from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)

AACT_HOST = "aact-db.ctti-clinicaltrials.org"
AACT_PORT = 5432
AACT_DB = "aact"
AACT_USER = "aact"
AACT_PASSWORD = "aact"  # public read-only
DEFAULT_PAGE_SIZE = 1000

AACT_STUDY_QUERY = """
    SELECT
        s.nct_id,
        s.brief_title,
        s.official_title,
        s.overall_status,
        s.study_type,
        s.phase,
        s.enrollment,
        s.enrollment_type,
        s.start_date,
        s.completion_date,
        s.results_first_submitted_date,
        s.last_update_posted_date,
        s.first_submitted_date,
        s.has_results,
        ls.name AS lead_sponsor_name,
        ls.agency_class AS lead_sponsor_class
    FROM studies s
    LEFT JOIN sponsors ls ON s.nct_id = ls.nct_id AND ls.lead_or_collaborator = 'lead'
    ORDER BY s.nct_id
    LIMIT %s OFFSET %s
"""

AACT_CONDITIONS_QUERY = """
    SELECT name FROM conditions WHERE nct_id = %s ORDER BY id
"""

AACT_INTERVENTIONS_QUERY = """
    SELECT intervention_type, intervention_name FROM interventions WHERE nct_id = %s ORDER BY id
"""


@dataclass
class AactClient:
    host: str = AACT_HOST
    port: int = AACT_PORT
    database: str = AACT_DB
    user: str = AACT_USER
    password: str = AACT_PASSWORD
    page_size: int = DEFAULT_PAGE_SIZE
    _conn: Any = field(default=None, repr=False)

    def __post_init__(self):
        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2 is required for AACT database access. "
                "Install with: pip install clinicaltrials-parser[aact]"
            )

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            import psycopg2

            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
        return self._conn

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    def _build_study(self, row: tuple) -> dict[str, Any]:
        conn = self._get_conn()
        cur = conn.cursor()
        nct_id = row[0]

        cur.execute(AACT_CONDITIONS_QUERY, (nct_id,))
        conditions = [r[0] for r in cur.fetchall()]

        cur.execute(AACT_INTERVENTIONS_QUERY, (nct_id,))
        arm_groups = []
        for itype, iname in cur.fetchall():
            arm_groups.append({"type": itype, "interventionNames": [iname]})

        cur.close()

        return {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": row[1],
                    "officialTitle": row[2],
                },
                "statusModule": {
                    "overallStatus": row[3],
                },
                "designModule": {
                    "studyType": row[4],
                    "phases": [row[5]] if row[5] else None,
                    "enrollmentInfo": {"count": row[6], "type": row[7]},
                },
                "conditionsModule": {
                    "conditions": conditions,
                },
                "armsInterventionsModule": {
                    "armGroups": arm_groups,
                },
                "sponsorCollaboratorsModule": {
                    "leadSponsor": {
                        "name": row[14],
                        "class": row[15],
                    },
                },
            },
            "hasResults": row[13],
        }

    def iter_studies(
        self,
        max_studies: int | None = None,
        offset: int = 0,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        conn = self._get_conn()
        fetched = 0
        page_offset = offset
        page_size = min(self.page_size, max_studies or self.page_size)

        while True:
            query = AACT_STUDY_QUERY
            cur = conn.cursor()
            cur.execute(query, (page_size, page_offset))
            rows = cur.fetchall()
            cur.close()

            if not rows:
                break

            for row in rows:
                yield self._build_study(row)
                fetched += 1
                if max_studies and fetched >= max_studies:
                    return

            page_offset += page_size

    def fetch_all_studies(
        self,
        max_studies: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        return list(self.iter_studies(max_studies=max_studies, **params))

    def get_total_count(self, **params: Any) -> int:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM studies")
        count = cur.fetchone()[0]
        cur.close()
        return count

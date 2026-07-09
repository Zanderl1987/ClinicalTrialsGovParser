from __future__ import annotations

from clinicaltrials_parser.models import Study


RAW_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT04000009",
            "orgStudyIdInfo": {"id": "ACP-103-055", "idType": None},
            "briefTitle": "Extension Study of Pimavanserin",
            "officialTitle": "A 52-Week Open-Label Extension Study of Pimavanserin",
        },
        "statusModule": {
            "statusVerifiedDate": "2022-03",
            "overallStatus": "TERMINATED",
        },
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "ACADIA Pharmaceuticals Inc.", "class": "INDUSTRY"}
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3"],
            "enrollmentInfo": {"count": 235, "type": "ACTUAL"},
        },
        "conditionsModule": {
            "conditions": ["Adjunctive Treatment of Major Depressive Disorder"]
        },
        "armsInterventionsModule": {
            "armGroups": [
                {
                    "label": "Drug - pimavanserin",
                    "type": "EXPERIMENTAL",
                    "interventionNames": ["Drug: Pimavanserin"],
                }
            ]
        },
    },
    "hasResults": True,
}


class TestStudy:
    def test_parse_from_api_response(self):
        study = Study(**RAW_STUDY)
        assert study.protocol_section is not None
        assert study.protocol_section.identification_module.nct_id == "NCT04000009"
        assert study.protocol_section.conditions_module.conditions == [
            "Adjunctive Treatment of Major Depressive Disorder"
        ]

    def test_flat_dict(self):
        study = Study(**RAW_STUDY)
        flat = study.flat_dict()
        assert flat["nct_id"] == "NCT04000009"
        assert flat["brief_title"] == "Extension Study of Pimavanserin"
        assert flat["overall_status"] == "TERMINATED"
        assert flat["study_type"] == "INTERVENTIONAL"
        assert flat["phases"] == ["PHASE3"]
        assert flat["conditions"] == ["Adjunctive Treatment of Major Depressive Disorder"]
        assert flat["lead_sponsor"] == "ACADIA Pharmaceuticals Inc."
        assert flat["enrollment_count"] == 235
        assert flat["has_results"] is True
        assert "intervention_types" in flat

    def test_flat_dict_empty_study(self):
        study = Study(protocolSection={})
        flat = study.flat_dict()
        assert flat["nct_id"] is None
        assert flat["study_type"] is None

    def test_flat_dict_no_results(self):
        raw = {**RAW_STUDY, "hasResults": False}
        study = Study(**raw)
        flat = study.flat_dict()
        assert flat["has_results"] is False

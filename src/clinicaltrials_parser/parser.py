from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from clinicaltrials_parser.client import ClinicalTrialsClient
from clinicaltrials_parser.models import Study
from clinicaltrials_parser.storage import StorageWriter

logger = logging.getLogger(__name__)


@dataclass
class StudyParser:
    client: ClinicalTrialsClient = field(default_factory=ClinicalTrialsClient)
    storage: StorageWriter = field(default_factory=StorageWriter)
    batch_size: int = 500
    resume_file: str | None = None
    _fetched_ids: set[str] = field(default_factory=set)

    def __post_init__(self):
        self._fetched_ids = set()
        if self.resume_file and os.path.exists(self.resume_file):
            self._load_resume_state()

    def _load_resume_state(self) -> None:
        try:
            with open(self.resume_file) as f:
                data = json.load(f)
            self._fetched_ids = set(data.get("fetched_ids", []))
            logger.info("Loaded resume state with %d fetched IDs", len(self._fetched_ids))
        except Exception as e:
            logger.warning("Could not load resume state: %s", e)

    def _save_resume_state(self) -> None:
        if not self.resume_file:
            return
        try:
            with open(self.resume_file, "w") as f:
                json.dump({"fetched_ids": list(self._fetched_ids)}, f)
        except Exception as e:
            logger.warning("Could not save resume state: %s", e)

    @property
    def is_resuming(self) -> bool:
        """True if a resume file was loaded and it contained prior progress."""
        return bool(self.resume_file and self._fetched_ids)

    def parse_all(
        self,
        max_studies: int | None = None,
        fields: str | None = None,
        flat: bool = True,
        on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
        **query_params: Any,
    ) -> Iterator[dict[str, Any]]:
        count = 0
        batch: list[dict[str, Any]] = []

        try:
            for raw_study in self.client.iter_studies(fields=fields, **query_params):
                if max_studies is not None and count >= max_studies:
                    break

                nct_id = (
                    raw_study.get("protocolSection", {})
                    .get("identificationModule", {})
                    .get("nctId")
                )
                if nct_id and nct_id in self._fetched_ids:
                    continue

                if flat:
                    try:
                        study = Study(**raw_study)
                        parsed = study.flat_dict()
                    except Exception as e:
                        logger.debug("Could not parse study %s: %s", nct_id, e)
                        parsed = self._safe_flat(raw_study)
                else:
                    parsed = raw_study

                if nct_id:
                    self._fetched_ids.add(nct_id)
                yield parsed
                count += 1

                if on_batch:
                    batch.append(parsed)
                    if len(batch) >= self.batch_size:
                        on_batch(batch)
                        batch = []

                if count % self.batch_size == 0:
                    self._save_resume_state()

            if on_batch and batch:
                on_batch(batch)
        finally:
            # Always persist progress, including on a mid-fetch exception, so a
            # crash doesn't lose track of everything already fetched this run.
            self._save_resume_state()

    @staticmethod
    def _safe_flat(raw: dict[str, Any]) -> dict[str, Any]:
        ps = raw.get("protocolSection") or {}
        id_mod = ps.get("identificationModule") or {}
        status_mod = ps.get("statusModule") or {}
        design_mod = ps.get("designModule") or {}
        cond_mod = ps.get("conditionsModule") or {}
        sponsor_mod = ps.get("sponsorCollaboratorsModule") or {}
        arms_mod = ps.get("armsInterventionsModule") or {}
        enrollment_info = design_mod.get("enrollmentInfo") or {}
        intervention_types = list({
            i.get("type") for i in (arms_mod.get("interventions") or []) if i.get("type")
        })
        return {
            "nct_id": id_mod.get("nctId"),
            "brief_title": id_mod.get("briefTitle"),
            "official_title": id_mod.get("officialTitle"),
            "overall_status": status_mod.get("overallStatus"),
            "study_type": design_mod.get("studyType"),
            "phases": design_mod.get("phases"),
            "conditions": cond_mod.get("conditions"),
            "intervention_types": intervention_types or None,
            "lead_sponsor": (sponsor_mod.get("leadSponsor") or {}).get("name"),
            "enrollment_count": enrollment_info.get("count"),
            "has_results": raw.get("hasResults"),
        }

    def to_storage(
        self,
        output_path: str | Path,
        fmt: str = "jsonl",
        max_studies: int | None = None,
        fields: str | None = None,
        flat: bool = True,
        progress_callback: Callable[[int], None] | None = None,
        **query_params: Any,
    ) -> Path:
        output_path = Path(output_path)
        self.storage.fmt = fmt
        self.storage.output_path = output_path
        self.storage.open(resume=self.is_resuming)

        written = 0
        try:
            for parsed in self.parse_all(max_studies=max_studies, fields=fields, flat=flat, **query_params):
                self.storage.write(parsed)
                written += 1
                if progress_callback:
                    progress_callback(1)
        finally:
            self.storage.close()

        logger.info("Wrote %d studies to %s", written, output_path)
        return output_path

    def stats(self, **query_params: Any) -> dict[str, Any]:
        return {
            "total_count": self.client.get_total_count(**query_params),
            "fetched_so_far": len(self._fetched_ids),
        }

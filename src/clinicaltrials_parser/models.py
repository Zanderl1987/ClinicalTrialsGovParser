from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StudyIdInfo(BaseModel):
    id_type: str | None = Field(None, alias="idType")
    id_value: str | None = Field(None, alias="idValue")


class IdentificationModule(BaseModel):
    nct_id: str | None = Field(None, alias="nctId")
    org_study_id_info: StudyIdInfo | None = Field(None, alias="orgStudyIdInfo")
    secondary_id_infos: list[StudyIdInfo] | None = Field(None, alias="secondaryIdInfos")
    brief_title: str | None = Field(None, alias="briefTitle")
    official_title: str | None = Field(None, alias="officialTitle")
    acronym: str | None = None


class StatusModule(BaseModel):
    status_verified_date: str | None = Field(None, alias="statusVerifiedDate")
    overall_status: str | None = Field(None, alias="overallStatus")
    last_known_status: str | None = Field(None, alias="lastKnownStatus")
    delayed_posting: bool | None = Field(None, alias="delayedPosting")
    expanded_access_info: dict[str, Any] | None = Field(None, alias="expandedAccessInfo")


class SponsorLead(BaseModel):
    name: str | None = None
    class_field: str | None = Field(None, alias="class")


class Collaborator(BaseModel):
    name: str | None = None
    class_field: str | None = Field(None, alias="class")


class SponsorCollaboratorsModule(BaseModel):
    lead_sponsor: SponsorLead | None = Field(None, alias="leadSponsor")
    collaborators: list[Collaborator] | None = None


class OversightModule(BaseModel):
    oversight_authority: list[str] | None = Field(None, alias="oversightAuthority")
    oversight_has_dmc: bool | None = Field(None, alias="oversightHasDmc")
    is_fda_regulated_drug: bool | None = Field(None, alias="isFdaRegulatedDrug")
    is_fda_regulated_device: bool | None = Field(None, alias="isFdaRegulatedDevice")


class MaskingInfo(BaseModel):
    masking: str | None = None


class DesignInfo(BaseModel):
    study_purpose: str | None = Field(None, alias="studyPurpose")
    intervention_model: str | None = Field(None, alias="interventionModel")
    intervention_model_description: str | None = Field(None, alias="interventionModelDescription")
    primary_purpose: str | None = Field(None, alias="primaryPurpose")
    observational_model: str | None = Field(None, alias="observationalModel")
    time_perspective: str | None = Field(None, alias="timePerspective")
    masking_info: MaskingInfo | None = Field(None, alias="maskingInfo")
    allocation: str | None = None


class DesignModule(BaseModel):
    study_type: str | None = Field(None, alias="studyType")
    design_info: DesignInfo | None = Field(None, alias="designInfo")
    target_duration: str | None = Field(None, alias="targetDuration")
    phases: list[str] | None = None
    enrollment_info: dict[str, Any] | None = Field(None, alias="enrollmentInfo")


class OutcomeMeasure(BaseModel):
    type: str | None = None
    title: str | None = None
    description: str | None = None
    time_frame: str | None = Field(None, alias="timeFrame")
    population_description: str | None = Field(None, alias="populationDescription")
    measure_param: str | None = Field(None, alias="measureParam")


class OutcomeModule(BaseModel):
    primary_outcomes: list[OutcomeMeasure] | None = Field(None, alias="primaryOutcomes")
    secondary_outcomes: list[OutcomeMeasure] | None = Field(None, alias="secondaryOutcomes")
    other_outcomes: list[OutcomeMeasure] | None = Field(None, alias="otherOutcomes")


class EligibilityModule(BaseModel):
    eligibility_criteria: str | None = Field(None, alias="eligibilityCriteria")
    healthy_volunteers: bool | None = Field(None, alias="healthyVolunteers")
    sex: str | None = None
    minimum_age: str | None = Field(None, alias="minimumAge")
    maximum_age: str | None = Field(None, alias="maximumAge")
    std_ages: list[str] | None = Field(None, alias="stdAges")
    study_population_description: str | None = Field(None, alias="studyPopulationDescription")
    sampling_method: str | None = Field(None, alias="samplingMethod")


class Location(BaseModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = Field(None, alias="zip")
    country: str | None = None
    status: str | None = None


class ContactsLocationsModule(BaseModel):
    locations: list[Location] | None = None
    overall_officials: list[dict[str, Any]] | None = Field(None, alias="overallOfficials")
    central_contacts: list[dict[str, Any]] | None = Field(None, alias="centralContacts")


class ConditionsModule(BaseModel):
    conditions: list[str] | None = None
    keywords: list[str] | None = None


class ArmGroup(BaseModel):
    type: str | None = None
    label: str | None = None
    description: str | None = None
    intervention_names: list[str] | None = Field(None, alias="interventionNames")


class Intervention(BaseModel):
    type: str | None = None
    name: str | None = None
    description: str | None = None
    arm_group_labels: list[str] | None = Field(None, alias="armGroupLabels")


class ArmsInterventionsModule(BaseModel):
    arm_groups: list[ArmGroup] | None = Field(None, alias="armGroups")
    interventions: list[Intervention] | None = None


class Reference(BaseModel):
    pmid: str | None = None
    citation: str | None = None


class ReferencesModule(BaseModel):
    references: list[Reference] | None = None


class DescriptionModule(BaseModel):
    brief_summary: str | None = Field(None, alias="briefSummary")


class IpdSharingStatementModule(BaseModel):
    ipd_sharing: str | None = Field(None, alias="ipdSharing")


class ProtocolSection(BaseModel):
    identification_module: IdentificationModule | None = Field(None, alias="identificationModule")
    status_module: StatusModule | None = Field(None, alias="statusModule")
    sponsor_collaborators_module: SponsorCollaboratorsModule | None = Field(None, alias="sponsorCollaboratorsModule")
    oversight_module: OversightModule | None = Field(None, alias="oversightModule")
    description_module: DescriptionModule | None = Field(None, alias="descriptionModule")
    conditions_module: ConditionsModule | None = Field(None, alias="conditionsModule")
    design_module: DesignModule | None = Field(None, alias="designModule")
    arms_interventions_module: ArmsInterventionsModule | None = Field(None, alias="armsInterventionsModule")
    outcomes_module: OutcomeModule | None = Field(None, alias="outcomesModule")
    eligibility_module: EligibilityModule | None = Field(None, alias="eligibilityModule")
    contacts_locations_module: ContactsLocationsModule | None = Field(None, alias="contactsLocationsModule")
    ipd_sharing_statement_module: IpdSharingStatementModule | None = Field(None, alias="ipdSharingStatementModule")
    references_module: ReferencesModule | None = Field(None, alias="referencesModule")


class BaselineMeasure(BaseModel):
    title: str | None = None
    description: str | None = None
    param: str | None = None
    classes: list[dict[str, Any]] | None = None


class BaselineModule(BaseModel):
    baseline_population_description: str | None = Field(None, alias="baselinePopulationDescription")
    baseline_measures: list[BaselineMeasure] | None = Field(None, alias="baselineMeasures")


class OutcomeMeasureResult(BaseModel):
    title: str | None = None
    description: str | None = None
    units: str | None = None
    param: str | None = None
    dispersion: str | None = None
    groups: list[dict[str, Any]] | None = None


class OutcomeResultsModule(BaseModel):
    outcome_measures: list[OutcomeMeasureResult] | None = Field(None, alias="outcomeMeasures")


class AdverseEvent(BaseModel):
    term: str | None = None
    organ_system: str | None = Field(None, alias="organSystem")
    source_vocabulary: str | None = Field(None, alias="sourceVocabulary")
    assessment_type: str | None = Field(None, alias="assessmentType")
    affected_arm_counts: list[dict[str, Any]] | None = Field(None, alias="affectedArmCounts")


class AdverseEventsModule(BaseModel):
    serious_adverse_events: list[AdverseEvent] | None = Field(None, alias="seriousAdverseEvents")
    other_adverse_events: list[AdverseEvent] | None = Field(None, alias="otherAdverseEvents")


class ResultsSection(BaseModel):
    baseline_module: BaselineModule | None = Field(None, alias="baselineModule")
    outcome_results_module: OutcomeResultsModule | None = Field(None, alias="outcomeResultsModule")
    adverse_events_module: AdverseEventsModule | None = Field(None, alias="adverseEventsModule")


class Study(BaseModel):
    protocol_section: ProtocolSection | None = Field(None, alias="protocolSection")
    results_section: ResultsSection | None = Field(None, alias="resultsSection")
    has_results: bool | None = Field(None, alias="hasResults")

    def flat_dict(self) -> dict[str, Any]:
        ps = self.protocol_section
        id_mod = ps.identification_module if ps else None
        status_mod = ps.status_module if ps else None
        design_mod = ps.design_module if ps else None
        cond_mod = ps.conditions_module if ps else None
        sponsor_mod = ps.sponsor_collaborators_module if ps else None
        arms_mod = ps.arms_interventions_module if ps else None

        d: dict[str, Any] = {
            "nct_id": id_mod.nct_id if id_mod else None,
            "brief_title": id_mod.brief_title if id_mod else None,
            "official_title": id_mod.official_title if id_mod else None,
            "overall_status": status_mod.overall_status if status_mod else None,
            "study_type": design_mod.study_type if design_mod else None,
            "phases": design_mod.phases if design_mod else None,
            "conditions": cond_mod.conditions if cond_mod else None,
            "intervention_types": (
                list({i.type for i in (arms_mod.interventions or []) if i.type}) if arms_mod else None
            ),
            "lead_sponsor": (
                sponsor_mod.lead_sponsor.name if sponsor_mod and sponsor_mod.lead_sponsor else None
            ),
            "enrollment_count": (
                design_mod.enrollment_info.get("count")
                if design_mod and design_mod.enrollment_info
                else None
            ),
            "has_results": self.has_results,
        }
        return d


class StudySearchResponse(BaseModel):
    studies: list[Study] = []
    next_page_token: str | None = Field(None, alias="nextPageToken")
    total_count: int | None = None

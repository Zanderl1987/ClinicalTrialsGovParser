"""Turn the raw pull into one row per trial: label + registration-time features.

Leakage rules (see README): the downloaded record is the trial's *current* state, so
anything a trial only reports at or after its end is never a feature: enrollment count
(finished trials report the ACTUAL number), completion dates, whyStopped, hasResults,
last-update dates. Trials registered after they ended are flagged (`reg_after_end`)
so the model step can drop them. Site/country counts are kept but tagged `site_*` so they can be
dropped as a group in the ablation check (sites can be added/removed mid-trial).

Output: data/trial_stopping/trials.parquet (every interventional trial, all statuses;
the label is defined in the model step).

Usage:
    python analysis/trial_stopping/build_dataset.py
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

import polars as pl

RAW = Path("data/trial_stopping/studies.jsonl.gz")
OUT = Path("data/trial_stopping/trials.parquet")

# Multi-hot columns keep only categories present in at least this many trials.
MIN_CATEGORY_COUNT = 2000

# Sponsor track record. A finished trial's outcome counts as public this long after its
# completion date (status updates lag the end of a trial), and never before the trial
# itself was registered.
OUTCOME_PUBLIC_LAG_DAYS = 180
# Shrinks the stop rate of sponsors with few past trials toward the overall rate:
# rate = (stopped + K * PRIOR) / (finished + K). PRIOR is the ~2007-2021 average (11-12%).
SHRINK_K = 10
SHRINK_PRIOR = 0.12

_AGE = re.compile(r"(\d+(?:\.\d+)?)\s*(year|month|week|day|hour|minute)", re.I)
_AGE_TO_YEARS = {"year": 1, "month": 1 / 12, "week": 1 / 52, "day": 1 / 365, "hour": 1 / 8760, "minute": 1 / 525600}


def _age_years(s: str | None) -> float | None:
    if not s:
        return None
    m = _AGE.search(s)
    return float(m.group(1)) * _AGE_TO_YEARS[m.group(2).lower()] if m else None


def _date(s: str | None) -> str | None:
    """Normalise 'YYYY-MM' / 'YYYY-MM-DD' to a full date (month-only -> the 1st)."""
    if not s:
        return None
    return s if len(s) == 10 else f"{s[:7]}-01" if len(s) == 7 else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def parse(rec: dict) -> dict:
    ps = rec.get("protocolSection", {})
    st = ps.get("statusModule", {})
    sp = ps.get("sponsorCollaboratorsModule", {})
    ov = ps.get("oversightModule", {})
    de = ps.get("designModule", {})
    di = de.get("designInfo", {})
    ai = ps.get("armsInterventionsModule", {})
    oc = ps.get("outcomesModule", {})
    el = ps.get("eligibilityModule", {})
    co = ps.get("conditionsModule", {})
    ds = rec.get("derivedSection", {})
    locs = ps.get("contactsLocationsModule", {}).get("locations", []) or []
    collabs = sp.get("collaborators", []) or []
    countries = {loc.get("country") for loc in locs if loc.get("country")}
    crit = el.get("eligibilityCriteria") or ""

    return {
        "nct_id": ps.get("identificationModule", {}).get("nctId"),
        "overall_status": st.get("overallStatus"),
        "why_stopped": st.get("whyStopped"),  # analysis only, never a feature
        "first_submit_date": _date(st.get("studyFirstSubmitDate")),
        "start_date": _date(st.get("startDateStruct", {}).get("date")),
        # Filter only, never a feature: used to drop trials registered after they ended.
        "primary_completion_date": _date(
            (st.get("primaryCompletionDateStruct") or st.get("completionDateStruct") or {}).get("date")
        ),
        "completion_date": _date((st.get("completionDateStruct") or {}).get("date")),  # history only
        "sponsor_name": sp.get("leadSponsor", {}).get("name"),
        "sponsor_class": sp.get("leadSponsor", {}).get("class"),
        "responsible_party": sp.get("responsibleParty", {}).get("type"),
        "n_collaborators": len(collabs),
        "collab_industry": any(c.get("class") == "INDUSTRY" for c in collabs),
        "has_dmc": ov.get("oversightHasDmc"),
        "fda_drug": ov.get("isFdaRegulatedDrug"),
        "fda_device": ov.get("isFdaRegulatedDevice"),
        "phase": "+".join(de.get("phases") or []) or None,
        "allocation": di.get("allocation"),
        "intervention_model": di.get("interventionModel"),
        "primary_purpose": di.get("primaryPurpose"),
        "masking": di.get("maskingInfo", {}).get("masking"),
        "n_masked_roles": len(di.get("maskingInfo", {}).get("whoMasked") or []),
        "n_arms": len(ai.get("armGroups") or []),
        "n_interventions": len(ai.get("interventions") or []),
        "has_placebo_arm": any(a.get("type") == "PLACEBO_COMPARATOR" for a in ai.get("armGroups") or []),
        "intervention_types": sorted({i.get("type") for i in ai.get("interventions") or [] if i.get("type")}),
        "n_primary_outcomes": len(oc.get("primaryOutcomes") or []),
        "n_secondary_outcomes": len(oc.get("secondaryOutcomes") or []),
        "elig_chars": len(crit),
        "elig_lines": sum(1 for line in crit.splitlines() if line.strip()),
        "sex": el.get("sex"),
        "healthy_volunteers": el.get("healthyVolunteers"),
        "min_age_years": _age_years(el.get("minimumAge")),
        "max_age_years": _age_years(el.get("maximumAge")),
        "n_conditions": len(co.get("conditions") or []),
        "n_keywords": len(co.get("keywords") or []),
        "summary_chars": len(ps.get("descriptionModule", {}).get("briefSummary") or ""),
        "mesh_areas": sorted({a["term"] for a in ds.get("conditionBrowseModule", {}).get("ancestors") or []}
                             | {m["term"] for m in ds.get("conditionBrowseModule", {}).get("meshes") or []}),
        "site_n": len(locs),
        "site_n_countries": len(countries),
        "site_us": "United States" in countries,
    }


def add_sponsor_history(df: pl.DataFrame) -> pl.DataFrame:
    """Point-in-time sponsor track record, as of each trial's registration date.

    Counts only the same sponsor's *other* trials, and only what was knowable strictly
    before this trial registered:
      sponsor_prior_registered  trials the sponsor had registered before
      sponsor_prior_finished    of its trials, how many had a public TERMINATED/COMPLETED outcome
      sponsor_prior_stopped     ... of which TERMINATED
      sponsor_prior_stop_rate   shrunk stop rate (see SHRINK_K / SHRINK_PRIOR)
    Current statuses are used, so a trial counts only if it is finished *today* and its
    end (+ lag) came before the new registration; a trial still running then is never
    counted, whatever it later became.
    """
    key = pl.col("sponsor_name").str.to_lowercase().str.strip_chars()
    df = df.with_columns(key.alias("_sponsor")).with_row_index("_row")
    reg = df.filter(pl.col("_sponsor").is_not_null() & pl.col("first_submit_date").is_not_null())

    known = (
        reg.filter(pl.col("overall_status").is_in(["TERMINATED", "COMPLETED"]))
        .with_columns(pl.coalesce("completion_date", "primary_completion_date").alias("_end"))
        .filter(pl.col("_end").is_not_null())
        .with_columns(
            pl.max_horizontal(
                pl.col("_end") + pl.duration(days=OUTCOME_PUBLIC_LAG_DAYS), pl.col("first_submit_date")
            ).alias("_t")
        )
        .group_by("_sponsor", "_t")
        .agg(_n=pl.len(), _s=(pl.col("overall_status") == "TERMINATED").sum())
        .sort("_sponsor", "_t")
        .with_columns(
            pl.col("_n").cum_sum().over("_sponsor").alias("sponsor_prior_finished"),
            pl.col("_s").cum_sum().over("_sponsor").alias("sponsor_prior_stopped"),
        )
        .select("_sponsor", "_t", "sponsor_prior_finished", "sponsor_prior_stopped")
    )
    registered = (
        reg.group_by("_sponsor", pl.col("first_submit_date").alias("_t"))
        .len()
        .sort("_sponsor", "_t")
        .with_columns(pl.col("len").cum_sum().over("_sponsor").alias("sponsor_prior_registered"))
        .select("_sponsor", "_t", "sponsor_prior_registered")
    )

    left = reg.select("_row", "_sponsor", pl.col("first_submit_date").alias("_t")).sort("_t")
    for right in (known, registered):
        left = left.join_asof(right.sort("_t"), on="_t", by="_sponsor", strategy="backward",
                              allow_exact_matches=False,  # strictly before registration
                              check_sortedness=False)  # both sides sorted by _t, so within each sponsor too
    cols = ["sponsor_prior_registered", "sponsor_prior_finished", "sponsor_prior_stopped"]
    df = df.join(left.select("_row", *cols), on="_row", how="left").with_columns(
        [pl.col(c).fill_null(0) for c in cols]
    )
    return df.with_columns(
        ((pl.col("sponsor_prior_stopped") + SHRINK_K * SHRINK_PRIOR)
         / (pl.col("sponsor_prior_finished") + SHRINK_K)).alias("sponsor_prior_stop_rate")
    ).drop("_row", "_sponsor")


def main() -> int:
    with gzip.open(RAW, "rt", encoding="utf-8") as f:
        rows = [parse(json.loads(line)) for line in f]
    print(f"parsed {len(rows):,} records", flush=True)

    df = pl.DataFrame(rows, infer_schema_length=None)
    df = df.unique("nct_id", keep="first")
    df = df.with_columns(
        pl.col("first_submit_date").str.to_date(strict=False),
        pl.col("start_date").str.to_date(strict=False),
        pl.col("primary_completion_date").str.to_date(strict=False),
        pl.col("completion_date").str.to_date(strict=False),
    ).with_columns(
        # Registered after the trial ended: the outcome was already known at registration.
        (pl.col("first_submit_date") > pl.col("primary_completion_date")).fill_null(True).alias("reg_after_end"),
        pl.col("first_submit_date").dt.year().alias("reg_year"),
        pl.col("start_date").dt.year().alias("start_year"),
        # Negative = registered before the trial started (prospective registration).
        (pl.col("first_submit_date") - pl.col("start_date")).dt.total_days().alias("reg_lag_days"),
    )

    df = add_sponsor_history(df)

    # Multi-hot for list columns, restricted to common categories.
    for col, prefix in [("intervention_types", "itype_"), ("mesh_areas", "mesh_")]:
        counts = Counter(v for vs in df[col].to_list() for v in (vs or []))
        keep = sorted(k for k, n in counts.items() if n >= MIN_CATEGORY_COUNT)
        df = df.with_columns(
            [pl.col(col).list.contains(k).fill_null(False).alias(prefix + _slug(k)) for k in keep]
        ).drop(col)
        print(f"{col}: kept {len(keep)} of {len(counts)} categories", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT)
    print(f"wrote {df.height:,} rows x {df.width} cols -> {OUT}", flush=True)
    print(df.group_by("overall_status").len().sort("len", descending=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

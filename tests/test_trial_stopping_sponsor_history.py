"""Point-in-time rules for the trial-stopping sponsor track record."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis" / "trial_stopping"))
from build_dataset import SHRINK_K, SHRINK_PRIOR, add_sponsor_history  # noqa: E402


def _trial(nct, sponsor, status, registered, ended=None):
    return {"nct_id": nct, "sponsor_name": sponsor, "overall_status": status,
            "first_submit_date": registered, "completion_date": ended, "primary_completion_date": ended}


@pytest.fixture
def history() -> dict[str, dict]:
    rows = [
        # Ends 2011-01-01 -> public 2011-06-30 (180-day lag).
        _trial("A1", "Acme", "TERMINATED", date(2010, 1, 1), date(2011, 1, 1)),
        # Registered after A1 ended but before its outcome is public: must not see it.
        _trial("A2", "Acme", "COMPLETED", date(2011, 3, 1), date(2011, 12, 1)),
        # Sees A1 (stopped); A2's outcome is public only from 2012-05-29.
        _trial("A3", "  ACME ", "RECRUITING", date(2012, 1, 1)),
        # Registered after it ended: public from its registration day, not earlier.
        _trial("A4", "Acme", "COMPLETED", date(2013, 1, 1), date(2012, 6, 1)),
        # Same-day registration as A4: strictly-before rule excludes A4.
        _trial("A5", "Acme", "RECRUITING", date(2013, 1, 1)),
        _trial("A6", "Acme", "RECRUITING", date(2013, 1, 2)),
        # Other sponsor: sees none of Acme's trials.
        _trial("B1", "Beta", "RECRUITING", date(2020, 1, 1)),
    ]
    df = pl.DataFrame(rows)
    return {r["nct_id"]: r for r in add_sponsor_history(df).iter_rows(named=True)}


def test_outcome_not_visible_before_public_lag(history):
    assert history["A2"]["sponsor_prior_finished"] == 0
    assert history["A2"]["sponsor_prior_registered"] == 1


def test_outcome_visible_after_lag(history):
    assert history["A3"]["sponsor_prior_finished"] == 1
    assert history["A3"]["sponsor_prior_stopped"] == 1
    assert history["A3"]["sponsor_prior_registered"] == 2  # sponsor name is normalised


def test_retrospective_registration_not_visible_same_day(history):
    # By 2013-01-01: A1 and A2 are public; A4 (registered that very day) is not.
    assert history["A5"]["sponsor_prior_finished"] == 2
    assert history["A5"]["sponsor_prior_registered"] == 3
    assert history["A6"]["sponsor_prior_finished"] == 3
    assert history["A6"]["sponsor_prior_registered"] == 5


def test_sponsors_do_not_mix_and_rate_shrinks(history):
    assert history["B1"]["sponsor_prior_finished"] == 0
    assert history["B1"]["sponsor_prior_stop_rate"] == pytest.approx(SHRINK_PRIOR)
    assert history["A3"]["sponsor_prior_stop_rate"] == pytest.approx((1 + SHRINK_K * SHRINK_PRIOR) / (1 + SHRINK_K))

"""Predict TERMINATED vs COMPLETED from registration-time features.

    python analysis/trial_stopping/model.py cohorts            # step 1: base rates per year
    python analysis/trial_stopping/model.py fit --train 2007-2014 --test 2015-2017 [--scope prospective_us]

`fit` trains on trials registered in the train years and scores on the test years (a
time split, never random). Reports average precision (AP, the area under the
precision-recall curve; a no-skill model scores the base rate) and ROC AUC, with
bootstrap 95% intervals, for:
  - base rate
  - logistic regression on phase + sponsor class only
  - gradient-boosted trees on every safe feature
  - the same trees without the site_* features (leakage check)
plus permutation importance for the full model. Results go to
analysis/trial_stopping/results/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

DATA = Path("data/trial_stopping/trials.parquet")
RESULTS = Path("analysis/trial_stopping/results")
SEED = 0

RUNNING = {"NOT_YET_RECRUITING", "RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "SUSPENDED"}

# Never features: identifiers, the label, post-outcome text, raw dates (years are kept).
NOT_FEATURES = {"nct_id", "overall_status", "why_stopped", "first_submit_date", "start_date", "sponsor_name", "y",
                "primary_completion_date", "reg_after_end"}
CATEGORICAL = ["sponsor_class", "responsible_party", "phase", "allocation", "intervention_model",
               "primary_purpose", "masking", "sex"]


def _years(spec: str) -> tuple[int, int]:
    a, _, b = spec.partition("-")
    return int(a), int(b or a)


def cohorts(df: pl.DataFrame) -> None:
    t = (
        df.filter(pl.col("reg_year").is_between(2000, 2025))
        .group_by("reg_year")
        .agg(
            n=pl.len(),
            running=pl.col("overall_status").is_in(RUNNING).mean(),
            unknown=(pl.col("overall_status") == "UNKNOWN").mean(),
            withdrawn=(pl.col("overall_status") == "WITHDRAWN").mean(),
            n_labeled=pl.col("overall_status").is_in(["TERMINATED", "COMPLETED"]).sum(),
            term_rate=(pl.col("overall_status") == "TERMINATED").sum()
            / pl.col("overall_status").is_in(["TERMINATED", "COMPLETED"]).sum(),
        )
        .sort("reg_year")
    )
    with pl.Config(tbl_rows=40, float_precision=3):
        print(t)
    lab = df.filter(pl.col("overall_status").is_in(["TERMINATED", "COMPLETED"]))
    for col in ["phase", "sponsor_class"]:
        with pl.Config(tbl_rows=20, float_precision=3):
            print(lab.group_by(col).agg(n=pl.len(), term_rate=(pl.col("overall_status") == "TERMINATED").mean())
                  .sort("n", descending=True))


# Scopes: "all" = every labeled trial; "prospective" drops trials registered after they
# ended (outcome already known); "prospective_us" also keeps only trials with a US site,
# where status reporting is legally required (non-US trials go UNKNOWN 5x as often, so
# their stops are under-recorded).
SCOPES = {
    "all": pl.lit(True),
    "prospective": ~pl.col("reg_after_end"),
    "prospective_us": ~pl.col("reg_after_end") & pl.col("site_us"),
}


def _frame(df: pl.DataFrame, lo: int, hi: int, scope: str) -> pd.DataFrame:
    d = (
        df.filter(pl.col("reg_year").is_between(lo, hi) & pl.col("overall_status").is_in(["TERMINATED", "COMPLETED"])
                  & SCOPES[scope])
        .with_columns(y=(pl.col("overall_status") == "TERMINATED").cast(pl.Int8))
        .to_pandas()
    )
    for c in CATEGORICAL:
        d[c] = d[c].fillna("MISSING").astype("category")
    for c in d.columns:
        if d[c].dtype == bool or d[c].dtype == object and c not in NOT_FEATURES:
            d[c] = d[c].astype("float")  # nullable booleans -> 0/1/NaN
    return d


def _align_categories(train: pd.DataFrame, test: pd.DataFrame) -> None:
    for c in CATEGORICAL:
        cats = train[c].cat.categories
        test[c] = pd.Categorical(test[c].astype(str).where(test[c].astype(str).isin(cats), "MISSING"),
                                 categories=cats.union(["MISSING"]))
        train[c] = train[c].cat.set_categories(cats.union(["MISSING"]))


def _boot(y: np.ndarray, p: np.ndarray, n: int = 500) -> dict:
    rng = np.random.default_rng(SEED)
    ap, auc = [], []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        ap.append(average_precision_score(y[i], p[i]))
        auc.append(roc_auc_score(y[i], p[i]))
    return {
        "ap": average_precision_score(y, p), "ap_ci": [float(np.percentile(ap, 2.5)), float(np.percentile(ap, 97.5))],
        "auc": roc_auc_score(y, p) if len(set(p)) > 1 else 0.5,
        "auc_ci": [float(np.percentile(auc, 2.5)), float(np.percentile(auc, 97.5))],
    }


def _gbm() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=1.0,
        categorical_features="from_dtype", early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=30, scoring="average_precision", random_state=SEED,
    )


def fit(df: pl.DataFrame, train_spec: str, test_spec: str, scope: str) -> None:
    train, test = _frame(df, *_years(train_spec), scope), _frame(df, *_years(test_spec), scope)
    _align_categories(train, test)
    feats = [c for c in train.columns if c not in NOT_FEATURES]
    no_site = [c for c in feats if not c.startswith("site_")]
    y_tr, y_te = train["y"].to_numpy(), test["y"].to_numpy()
    print(f"scope: {scope}")
    print(f"train {train_spec}: {len(train):,} trials, {y_tr.mean():.3f} terminated")
    print(f"test  {test_spec}: {len(test):,} trials, {y_te.mean():.3f} terminated")
    print(f"{len(feats)} features")

    out: dict = {"scope": scope, "train": train_spec, "test": test_spec, "n_train": len(train), "n_test": len(test),
                 "base_rate_test": float(y_te.mean()), "models": {}}

    out["models"]["base_rate"] = _boot(y_te, np.full(len(y_te), y_tr.mean()))

    simple = pd.get_dummies(pd.concat([train, test])[["phase", "sponsor_class"]].astype(str), dtype=float)
    lr = LogisticRegression(max_iter=2000).fit(simple.iloc[: len(train)], y_tr)
    out["models"]["logistic_phase_sponsor"] = _boot(y_te, lr.predict_proba(simple.iloc[len(train):])[:, 1])

    gbm = _gbm().fit(train[feats], y_tr)
    p_full = gbm.predict_proba(test[feats])[:, 1]
    out["models"]["gbm_all"] = _boot(y_te, p_full)

    gbm_ns = _gbm().fit(train[no_site], y_tr)
    out["models"]["gbm_no_sites"] = _boot(y_te, gbm_ns.predict_proba(test[no_site])[:, 1])

    for name, m in out["models"].items():
        print(f"{name:24s} AP {m['ap']:.3f} [{m['ap_ci'][0]:.3f}, {m['ap_ci'][1]:.3f}]   "
              f"AUC {m['auc']:.3f} [{m['auc_ci'][0]:.3f}, {m['auc_ci'][1]:.3f}]")

    # Precision among the top-k% highest-risk test trials: the "watch list" view.
    order = np.argsort(-p_full)
    out["top_k_precision"] = {f"top_{k}pct": float(y_te[order[: max(1, len(order) * k // 100)]].mean())
                              for k in (1, 5, 10, 20)}
    print("precision in top-k% of gbm_all risk:", {k: round(v, 3) for k, v in out["top_k_precision"].items()})

    sample = test.sample(min(len(test), 20000), random_state=SEED)
    pi = permutation_importance(gbm, sample[feats], sample["y"], scoring="average_precision",
                                n_repeats=3, random_state=SEED, n_jobs=-1)
    imp = sorted(zip(feats, pi.importances_mean), key=lambda t: -t[1])
    out["importance_ap_drop"] = {f: float(v) for f, v in imp[:25]}
    print("top features (AP drop when shuffled):")
    for f, v in imp[:15]:
        print(f"  {f:32s} {v:+.4f}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"fit_{scope}_train{train_spec}_test{test_spec}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"-> {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cohorts")
    f = sub.add_parser("fit")
    f.add_argument("--train", required=True)
    f.add_argument("--test", required=True)
    f.add_argument("--scope", choices=list(SCOPES), default="prospective")
    args = ap.parse_args()
    df = pl.read_parquet(DATA)
    if args.cmd == "cohorts":
        cohorts(df)
    else:
        fit(df, args.train, args.test, args.scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())

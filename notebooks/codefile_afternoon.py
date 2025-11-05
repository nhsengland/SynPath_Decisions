"""
NHS MCDA for patient pathway prioritization

This script implements a small, flexible Multi-Criteria Decision Analysis (MCDA)
for ranking patients within each Speciality (department).

Primary rule from user:
  1) Group (primary key): Speciality
  2) Within each Speciality, rank using criteria in this order (but implemented
     as a weighted MCDA so weights can be tuned):
       - Complexity (numerical) — larger => higher priority
       - Acuity (1..5, higher = worse) — larger => higher priority
       - Vitals Trend (categorical) — priority order: Deteriorating > Stable > Improving

Features:
  - Normalises numeric values (optionally within each Speciality)
  - Maps Vitals Trend to an ordinal score
  - Allows adjustable weights for each criterion
  - Handles missing data sensibly
  - Returns a ranked DataFrame per Speciality and an overall ordering (Speciality groups preserved)

Usage example included at bottom.
"""

from typing import Dict, Any
import pandas as pd
import numpy as np
# Load ScenarioA data
from pathlib import Path
import pandas as pd
import os

DEFAULT_VITALS_ORDER = {
    'Deteriorating': 1.0,
    'Stable': 0.5,
    'Improving': 0.0
}


def normalize_series(s: pd.Series) -> pd.Series:
    """Min-max normalize a pandas Series to [0,1]. If constant, returns 0.5 for all.
    NaNs are left as NaN.
    """
    valid = s.dropna()
    if valid.empty:
        return s
    mn = valid.min()
    mx = valid.max()
    if mn == mx:
        # constant series; return 0.5 for known values
        out = s.copy()
        out.loc[s.notna()] = 0.5
        return out
    return (s - mn) / (mx - mn)


def compute_mcda_scores(
    df: pd.DataFrame,
    weights: Dict[str, float] = None,
    vitals_map: Dict[str, float] = None,
    normalize_within_Speciality: bool = True,
    complexity_col: str = 'Complexity',
    acuity_col: str = 'Acuity',
    vitals_col: str = 'Vitals Trend'
) -> pd.DataFrame:
    """
    Compute MCDA scores for patients.

    Parameters
    ----------
    df : DataFrame
        Must contain at least columns: Speciality, Complexity (numeric), Acuity (numeric), Vitals Trend (categorical)
    weights : dict
        Weights for components. Keys: 'complexity', 'acuity', 'vitals'. They need not sum to 1 (they will be normalized internally).
    vitals_map : dict
        Mapping from Vitals Trend string to numeric priority (higher => more urgent). If not provided, DEFAULT_VITALS_ORDER is used.
    normalize_within_Speciality : bool
        If True, normalize Complexity and Acuity within each Speciality group. Otherwise normalize globally.

    Returns
    -------
    DataFrame
        A copy of the DataFrame with columns: MCDA_score, mcda_rank_within_Speciality
    """
    df = df.copy()
    required = ['Speciality', complexity_col, acuity_col, vitals_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Input df is missing required columns: {missing}")

    if weights is None:
        weights = {'complexity': 0.5, 'acuity': 0.3, 'vitals': 0.2}
    # normalize weights to sum 1
    w_total = sum(weights.values())
    if w_total == 0:
        raise ValueError('Sum of weights must be > 0')
    weights = {k: v / w_total for k, v in weights.items()}

    if vitals_map is None:
        vitals_map = DEFAULT_VITALS_ORDER

    # Map vitals
    df['_vitals_score_raw'] = df[vitals_col].map(vitals_map)
    # If some vitals values were unseen, map them to median of known vitals scores
    if df['_vitals_score_raw'].isna().any():
        known_median = df['_vitals_score_raw'].median(skipna=True)
        df['_vitals_score_raw'].fillna(known_median, inplace=True)

    # Prepare normalized complexity and acuity
    if normalize_within_Speciality:
        norm_complexity = df.groupby('Speciality')[complexity_col].transform(lambda s: normalize_series(s))
        norm_acuity = df.groupby('Speciality')[acuity_col].transform(lambda s: normalize_series(s))
    else:
        norm_complexity = normalize_series(df[complexity_col])
        norm_acuity = normalize_series(df[acuity_col])

    # Missing numeric values: fill with group median (less likely, but safer)
    df['_norm_complexity'] = norm_complexity
    df['_norm_acuity'] = norm_acuity
    df['_norm_complexity'] = df.groupby('Speciality')['_norm_complexity'].apply(lambda s: s.fillna(s.median()))
    df['_norm_acuity'] = df.groupby('Speciality')['_norm_acuity'].apply(lambda s: s.fillna(s.median()))

    # MCDA linear additive score (higher = more urgent / higher priority)
    df['MCDA_score'] = (
        weights['complexity'] * df['_norm_complexity'] +
        weights['acuity'] * df['_norm_acuity'] +
        weights['vitals'] * df['_vitals_score_raw']
    )

    # Rank within Speciality: higher score -> rank 1 (most urgent)
    df['mcda_rank_within_Speciality'] = df.groupby('Speciality')['MCDA_score']\
        .rank(method='first', ascending=False).astype(int)

    # Add tie-breaker explanation column (use raw acuity, complexity, vitals for explanation)
    df['tie_breaker'] = df[[acuity_col, complexity_col, vitals_col]].apply(
        lambda row: f"Acuity={row[acuity_col]}|Complexity={row[complexity_col]}|Vitals={row[vitals_col]}", axis=1
    )

    # Clean helper columns
    df.drop(columns=['_vitals_score_raw', '_norm_complexity', '_norm_acuity'], inplace=True)

    return df


def rank_within_all_specialties(df_out: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a DataFrame ordered first by Speciality (alphabetical) and then by mcda_rank_within_Speciality.
    If you prefer a different Speciality order, reorder 'Speciality' before calling this function.
    """
    df = df_out.copy()
    df.sort_values(['Speciality', 'mcda_rank_within_Speciality'], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# -------------------------------
# Example usage (run as script or import functions)
# -------------------------------
if __name__ == '__main__':

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    data_dir = Path('../data/scenarioA/')
    paths = {
        "current": data_dir / "ScenarioA_patients_current.csv",
        "coming": data_dir / "ScenarioA_patients_coming.csv",
        "historic": data_dir / "ScenarioA_patients_historic.csv"
    }

    dfs = {k: pd.read_csv(v) for k, v in paths.items()}
    for name, df in dfs.items():
        df.columns = [c.strip() for c in df.columns]
        dfs[name] = df

    current = dfs["current"].copy()
    coming = dfs["coming"].copy()
    historic = dfs["historic"].copy()

    # Example: run MCDA on current patients
    weights = {'complexity': 0.5, 'acuity': 0.35, 'vitals': 0.15}
    ranked = compute_mcda_scores(current, weights=weights, normalize_within_Speciality=True)
    ordered = rank_within_all_specialties(ranked)
        # ordered.to_csv('mcda_ranked_patients.csv', index=False)

        # End of script

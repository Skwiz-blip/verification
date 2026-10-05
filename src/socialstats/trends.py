"""Analyse de l'evolution des performances dans le temps.

Deux methodes independantes sont appliquees, volontairement simples et
explicables a une direction :

  1. Comparaison debut / fin : mediane du premier tiers des publications contre
     mediane du dernier tiers (moities si l'effectif est faible). Lisible
     directement ("de 53 a 86 vues moyennes").
  2. Regression lineaire sur le rang chronologique. Fournit une pente et un R2,
     qui indiquent si la tendance est reguliere ou portee par quelques points.

Les medianes sont preferees aux moyennes pour limiter l'effet d'une publication
virale isolee en debut ou en fin de periode.

Si les deux methodes se contredisent, le verdict affiche est "Tendance
incertaine" avec les deux chiffres : mieux vaut afficher un doute qu'une
conclusion fausse qui pourrait couter une prime a quelqu'un.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from .config import Settings

logger = logging.getLogger(__name__)

PROGRESSION = "Performance en progression"
STABLE = "Performance stable"
DECLINE = "Performance en baisse"
UNCERTAIN = "Tendance incertaine"
INSUFFICIENT = "Donnees insuffisantes"


def _split_segments(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Decoupe une serie chronologique en segment de debut et segment de fin."""
    array = values.to_numpy(dtype=float)
    n = array.size
    size = max(2, n // 3) if n >= 9 else max(1, n // 2)
    return array[:size], array[-size:]


def analyze_series(
    values: pd.Series, settings: Settings
) -> dict[str, object]:
    """Analyse la tendance d'une serie deja triee chronologiquement."""
    clean = pd.to_numeric(values, errors="coerce").dropna()
    n = int(clean.size)

    if n < settings.trend.min_observations:
        return {
            "Publications": n,
            "Mediane debut": None,
            "Mediane fin": None,
            "Variation (%)": None,
            "Pente (vues/publication)": None,
            "R2": None,
            "Tendance": INSUFFICIENT,
            "Detail": f"{n} publication(s) < {settings.trend.min_observations} requises",
        }

    start, end = _split_segments(clean)
    median_start = float(np.median(start))
    median_end = float(np.median(end))
    change = (
        100.0 * (median_end - median_start) / median_start if median_start else float("nan")
    )

    ranks = np.arange(n, dtype=float)
    regression = scipy_stats.linregress(ranks, clean.to_numpy(dtype=float))
    slope = float(regression.slope)
    r_squared = float(regression.rvalue**2)

    threshold = settings.trend.change_threshold_pct
    if np.isnan(change):
        segment_verdict = STABLE
    elif change >= threshold:
        segment_verdict = PROGRESSION
    elif change <= -threshold:
        segment_verdict = DECLINE
    else:
        segment_verdict = STABLE

    # La regression n'est retenue comme signal que si elle est significative.
    if regression.pvalue < 0.05:
        regression_verdict = PROGRESSION if slope > 0 else DECLINE
    else:
        regression_verdict = STABLE

    if segment_verdict == regression_verdict:
        verdict = segment_verdict
        detail = f"Les deux methodes concordent (p={regression.pvalue:.3f})"
    elif STABLE in {segment_verdict, regression_verdict}:
        # Une methode voit un mouvement, l'autre non : on retient le mouvement
        # mais on le qualifie de faible plutot que de trancher au hasard.
        verdict = segment_verdict if segment_verdict != STABLE else regression_verdict
        detail = f"Signal faible : segments={segment_verdict}, regression={regression_verdict}"
    else:
        verdict = UNCERTAIN
        detail = f"Methodes contradictoires : segments={segment_verdict}, regression={regression_verdict}"

    return {
        "Publications": n,
        "Mediane debut": round(median_start, 1),
        "Mediane fin": round(median_end, 1),
        "Variation (%)": round(change, 1) if not np.isnan(change) else None,
        "Pente (vues/publication)": round(slope, 2),
        "R2": round(r_squared, 3),
        "Tendance": verdict,
        "Detail": detail,
    }


def analyze_trends(
    frame: pd.DataFrame,
    settings: Settings,
    group_columns: list[str] | None = None,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Calcule la tendance de chaque groupe (par defaut compte x plateforme)."""
    group_columns = group_columns or ["account_name", "platform"]
    if frame.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for keys, group in frame.groupby(group_columns, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        ordered = group.sort_values("published_at")
        row: dict[str, object] = {
            "Compte" if col == "account_name" else col.capitalize(): value
            for col, value in zip(group_columns, keys)
        }
        row["Secteur"] = group["sector"].iloc[0] if "sector" in group.columns else ""
        row["Debut"] = ordered["published_at"].min()
        row["Fin"] = ordered["published_at"].max()
        row.update(analyze_series(ordered[value_column], settings))
        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    counts = result["Tendance"].value_counts().to_dict()
    logger.info("Tendances : %s", counts)
    return result.sort_values("Compte").reset_index(drop=True)


def add_relative_trend(trends: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Compare la tendance de chaque compte a celle de sa plateforme.

    Pourquoi c'est necessaire : sur les donnees reelles, la quasi-totalite des
    comptes Instagram reculent sur la meme periode. Un recul simultane et
    generalise traduit une evolution de la plateforme (algorithme, saisonnalite,
    concurrence), pas treize echecs individuels. Sanctionner chaque Community
    Manager pour une baisse subie par tout le marche serait la comparaison
    injuste type.

    On ajoute donc une lecture RELATIVE : le compte a-t-il fait mieux ou moins
    bien que la tendance generale de sa plateforme sur la meme periode ?
    La reference est la MEDIANE des variations des comptes, robuste aux
    quelques comptes atypiques.
    """
    if trends.empty or "Variation (%)" not in trends.columns:
        return trends

    out = trends.copy()
    measurable = out["Variation (%)"].notna()
    if not measurable.any():
        return out

    platform_column = "Platform" if "Platform" in out.columns else None
    if platform_column is None:
        out["_ref"] = out.loc[measurable, "Variation (%)"].median()
        out["_n_ref"] = int(measurable.sum())
    else:
        grouped = out[measurable].groupby(platform_column)["Variation (%)"]
        out["_ref"] = grouped.transform("median")
        out["_n_ref"] = grouped.transform("size")

    out["Variation plateforme (%)"] = out["_ref"].round(1)
    out["Comptes de reference"] = out["_n_ref"]
    out["Ecart au marche (points)"] = (out["Variation (%)"] - out["_ref"]).round(1)

    threshold = settings.trend.change_threshold_pct
    min_reference = settings.sector.min_accounts

    def classify(row: pd.Series) -> str:
        gap = row["Ecart au marche (points)"]
        if pd.isna(gap):
            return "Non evaluable"
        # Une reference calculee sur trop peu de comptes ne represente pas
        # "le marche" : la comparaison serait affichee comme un fait alors
        # qu'elle repose sur deux ou trois observations.
        if pd.isna(row["_n_ref"]) or row["_n_ref"] < min_reference:
            return "Reference plateforme insuffisante"
        if gap >= threshold:
            return "Fait mieux que sa plateforme"
        if gap <= -threshold:
            return "Fait moins bien que sa plateforme"
        return "Conforme a sa plateforme"

    out["Tendance relative"] = out.apply(classify, axis=1)
    return out.drop(columns=["_ref", "_n_ref"])


def monthly_evolution(
    frame: pd.DataFrame, value_column: str = "views_organic_resolved"
) -> pd.DataFrame:
    """Serie mensuelle des performances, utilisee par les graphiques."""
    if frame.empty:
        return pd.DataFrame()

    out = frame.dropna(subset=["published_at"]).copy()
    out["mois"] = out["published_at"].dt.to_period("M").dt.to_timestamp()
    grouped = (
        out.groupby(["account_name", "mois"])[value_column]
        .agg(["count", "median", "mean"])
        .reset_index()
        .rename(
            columns={
                "account_name": "Compte",
                "count": "Publications",
                "median": "Vues medianes",
                "mean": "Vues moyennes",
            }
        )
    )
    return grouped

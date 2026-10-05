"""Statistiques descriptives par compte x plateforme x format.

Le module s'appelle `descriptive` et non `statistics` pour ne pas masquer le
module `statistics` de la bibliotheque standard.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings
from .outliers import describe

logger = logging.getLogger(__name__)

GROUP_COLUMNS = ["account_name", "platform", "format"]

COLUMN_LABELS = {
    "account_name": "Compte",
    "sector": "Secteur",
    "platform": "Plateforme",
    "format": "Format",
    "n": "Publications",
    "mean": "Moyenne",
    "median": "Mediane",
    "trimmed_mean_10": "Moyenne tronquee 10%",
    "std": "Ecart-type",
    "minimum": "Minimum",
    "maximum": "Maximum",
    "q1": "Q1",
    "q3": "Q3",
    "iqr": "IQR",
    "mad": "MAD",
    "cv": "Coef. variation",
    "skewness": "Asymetrie",
    "n_outliers": "Outliers",
    "n_outliers_high": "Outliers hauts",
    "n_outliers_low": "Outliers bas",
    "lower_bound": "Borne basse",
    "upper_bound": "Borne haute",
    "method": "Methode outliers",
}


def group_statistics(
    frame: pd.DataFrame,
    settings: Settings,
    value_column: str = "views_organic_resolved",
    group_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Calcule le portrait statistique de chaque groupe de comparaison."""
    group_columns = group_columns or GROUP_COLUMNS
    if frame.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for keys, group in frame.groupby(group_columns, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        stats = describe(
            group[value_column],
            method=settings.outliers.method,
            multiplier=settings.outliers.multiplier,
            mad_threshold=settings.outliers.mad_threshold,
        )
        row: dict[str, object] = dict(zip(group_columns, keys))
        row["sector"] = group["sector"].iloc[0] if "sector" in group.columns else ""
        row.update(stats.as_dict())

        engagement = describe(
            group["engagement_rate"],
            method=settings.outliers.method,
            multiplier=settings.outliers.multiplier,
            mad_threshold=settings.outliers.mad_threshold,
        )
        row["engagement_median"] = engagement.median
        row["engagement_mean"] = engagement.mean
        row["engagement_n"] = engagement.n

        row["reliability"] = settings.reliability.label(stats.n)
        row["sufficient"] = stats.n >= settings.min_observations
        row["period_start"] = group["published_at"].min()
        row["period_end"] = group["published_at"].max()
        rows.append(row)

    result = pd.DataFrame(rows).sort_values(group_columns).reset_index(drop=True)
    logger.info("Statistiques calculees pour %d groupe(s).", len(result))
    return result


def account_overview(frame: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Vue synthetique par compte, tous formats confondus."""
    if frame.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for account, group in frame.groupby("account_name"):
        stats = describe(
            group["views_organic_resolved"],
            method=settings.outliers.method,
            multiplier=settings.outliers.multiplier,
            mad_threshold=settings.outliers.mad_threshold,
        )
        rows.append(
            {
                "Compte": account,
                "Secteur": group["sector"].iloc[0],
                "Publications": stats.n,
                "Formats utilises": group["format"].nunique(),
                "Vues medianes": round(stats.median, 1),
                "Vues moyennes": round(stats.mean, 1),
                "Vues max": round(stats.maximum, 1),
                "Engagement median (%)": round(group["engagement_rate"].median(), 2),
                "Publications exceptionnelles": stats.n_outliers,
                "Debut": group["published_at"].min(),
                "Fin": group["published_at"].max(),
                "Fiabilite": settings.reliability.label(stats.n),
            }
        )
    return pd.DataFrame(rows).sort_values("Publications", ascending=False).reset_index(drop=True)


def to_readable(stats_frame: pd.DataFrame) -> pd.DataFrame:
    """Renomme et arrondit les colonnes pour un rapport lisible."""
    if stats_frame.empty:
        return stats_frame
    out = stats_frame.copy()
    numeric = out.select_dtypes(include="number").columns
    out[numeric] = out[numeric].round(2)
    return out.rename(columns=COLUMN_LABELS)

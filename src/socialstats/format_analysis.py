"""Comparaison des performances entre formats de contenu.

Les ratios sont calcules sur des MEDIANES et non des moyennes : sur un petit
nombre de Reels, une seule video virale suffirait a produire un ratio du type
"les Reels font 12 fois mieux" qui ne se reproduirait jamais.

Les formats reposant sur trop peu de publications sont conserves dans la table
mais explicitement marques, car c'est precisement la ou la tentation de
conclure trop vite est la plus forte.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings

logger = logging.getLogger(__name__)

REFERENCE_FORMAT = "photo"


def compare_formats(
    frame: pd.DataFrame,
    settings: Settings,
    group_columns: list[str] | None = None,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Compare les formats au sein de chaque groupe (par defaut par compte)."""
    group_columns = group_columns or ["account_name", "platform"]
    if frame.empty:
        return pd.DataFrame()

    usable = frame[~frame["format"].isin(settings.excluded_formats)]
    if usable.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for keys, group in usable.groupby(group_columns, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        by_format = (
            group.assign(_v=pd.to_numeric(group[value_column], errors="coerce"))
            .dropna(subset=["_v"])
            .groupby("format")
            .agg(
                publications=("_v", "size"),
                mediane=("_v", "median"),
                moyenne=("_v", "mean"),
                engagement=("engagement_rate", "median"),
            )
        )
        if by_format.empty:
            continue

        reference = (
            by_format.loc[REFERENCE_FORMAT, "mediane"]
            if REFERENCE_FORMAT in by_format.index
            else by_format["mediane"].min()
        )
        reference_label = (
            REFERENCE_FORMAT if REFERENCE_FORMAT in by_format.index else "format le moins performant"
        )

        for fmt, stats in by_format.iterrows():
            n = int(stats["publications"])
            rows.append(
                {
                    **{
                        "Compte" if col == "account_name" else col.capitalize(): value
                        for col, value in zip(group_columns, keys)
                    },
                    "Format": fmt,
                    "Publications": n,
                    "Vues medianes": round(float(stats["mediane"]), 1),
                    "Vues moyennes": round(float(stats["moyenne"]), 1),
                    "Engagement median (%)": round(float(stats["engagement"]), 2)
                    if pd.notna(stats["engagement"])
                    else None,
                    f"Ratio vs {reference_label}": round(float(stats["mediane"]) / reference, 2)
                    if reference
                    else None,
                    "Fiabilite": settings.reliability.label(n),
                }
            )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values(["Compte", "Vues medianes"], ascending=[True, False]).reset_index(
        drop=True
    )


def best_format_per_group(
    comparison: pd.DataFrame, min_publications: int = 5
) -> pd.DataFrame:
    """Identifie le format le plus performant de chaque compte.

    Les formats sous-representes sont ecartes du classement : designer un
    "meilleur format" sur 2 publications serait un artefact.
    """
    if comparison.empty:
        return pd.DataFrame()

    eligible = comparison[comparison["Publications"] >= min_publications]
    if eligible.empty:
        return pd.DataFrame()

    best = (
        eligible.sort_values("Vues medianes", ascending=False)
        .groupby("Compte", as_index=False)
        .first()[["Compte", "Format", "Publications", "Vues medianes", "Fiabilite"]]
        .rename(columns={"Format": "Meilleur format"})
    )
    return best.reset_index(drop=True)


def sector_format_summary(
    frame: pd.DataFrame, settings: Settings, value_column: str = "views_organic_resolved"
) -> pd.DataFrame:
    """Performance des formats par secteur, en ponderant chaque compte a parts egales."""
    if frame.empty:
        return pd.DataFrame()

    usable = frame[~frame["format"].isin(settings.excluded_formats)]
    if usable.empty:
        return pd.DataFrame()

    per_account = (
        usable.assign(_v=pd.to_numeric(usable[value_column], errors="coerce"))
        .dropna(subset=["_v"])
        .groupby(["sector", "format", "account_name"])["_v"]
        .median()
        .reset_index()
    )
    summary = (
        per_account.groupby(["sector", "format"])
        .agg(comptes=("account_name", "nunique"), mediane_des_medianes=("_v", "median"))
        .reset_index()
        .rename(
            columns={
                "sector": "Secteur",
                "format": "Format",
                "comptes": "Comptes",
                "mediane_des_medianes": "Mediane sectorielle",
            }
        )
    )
    summary["Mediane sectorielle"] = summary["Mediane sectorielle"].round(1)
    return summary.sort_values(["Secteur", "Mediane sectorielle"], ascending=[True, False]).reset_index(
        drop=True
    )

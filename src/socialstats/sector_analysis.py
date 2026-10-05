"""Benchmark sectoriel : secteur x plateforme x format.

Probleme central : un simple regroupement de toutes les publications d'un
secteur laisse le compte le plus prolifique dicter le benchmark. Dans les
donnees reelles analysees, un compte pese 133 photos quand un autre en pese 7 :
le "benchmark sectoriel" ne serait alors que le portrait du plus gros compte.

Methode principale retenue : MEDIANE DES MEDIANES.
  1. Pour chaque compte du secteur, calculer sa mediane de vues organiques.
  2. Prendre la mediane de ces medianes de comptes.
Chaque compte pese ainsi pour 1, quel que soit son volume de publications.

La version "poolee" (tous les posts melanges) est conservee EN PARALLELE comme
indicateur secondaire, explicitement etiquete "pondere par le volume". Les deux
lectures sont utiles : l'une decrit le compte typique, l'autre la publication
typique. Les afficher cote a cote evite de faire passer l'une pour l'autre.

Fiabilite : un benchmark n'est declare exploitable que s'il repose sur assez de
comptes ET assez de publications (seuils configurables). Un secteur a un seul
compte est toujours marque INSUFFISANT, par construction.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import Settings

logger = logging.getLogger(__name__)

SECTOR_GROUP = ["sector", "platform", "format"]
RELIABLE = "Exploitable"
INSUFFICIENT = "INSUFFISANT"


def build_sector_benchmark(
    frame: pd.DataFrame,
    settings: Settings,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Construit le benchmark de chaque secteur x plateforme x format."""
    if frame.empty:
        return pd.DataFrame()

    q1, q2, q3 = settings.objectives.quantiles
    min_per_account = settings.sector.min_observations_per_account
    rows: list[dict[str, object]] = []

    for keys, group in frame.groupby(SECTOR_GROUP, dropna=False):
        sector, platform, fmt = keys
        if fmt in settings.excluded_formats:
            continue

        values = pd.to_numeric(group[value_column], errors="coerce").dropna()
        if values.empty:
            continue

        per_account = (
            group.assign(_v=pd.to_numeric(group[value_column], errors="coerce"))
            .dropna(subset=["_v"])
            .groupby("account_name")["_v"]
            .agg(["count", "median"])
        )
        qualified = per_account[per_account["count"] >= min_per_account]

        n_accounts_total = int(per_account.shape[0])
        n_accounts_qualified = int(qualified.shape[0])
        n_publications = int(values.size)

        required = settings.sector.required_accounts(sector)
        reasons: list[str] = []
        if n_accounts_qualified < required:
            reasons.append(f"{n_accounts_qualified} compte(s) qualifie(s) < {required}")
        elif settings.sector.is_forced(sector):
            reasons.append(
                f"DEROGATION : categorie calculee sur {n_accounts_qualified} compte(s) "
                "seulement, a interpreter avec prudence"
            )
        if n_publications < settings.sector.min_publications:
            reasons.append(
                f"{n_publications} publication(s) < {settings.sector.min_publications}"
            )

        median_of_medians = (
            float(qualified["median"].median()) if n_accounts_qualified else float("nan")
        )
        mean_of_medians = (
            float(qualified["median"].mean()) if n_accounts_qualified else float("nan")
        )
        dispersion = (
            float(qualified["median"].std(ddof=1)) if n_accounts_qualified > 1 else float("nan")
        )

        engagement = pd.to_numeric(group["engagement_rate"], errors="coerce").dropna()

        rows.append(
            {
                "Secteur": sector,
                "Plateforme": platform,
                "Format": fmt,
                "Nombre comptes": n_accounts_total,
                "Comptes qualifies": n_accounts_qualified,
                "Publications": n_publications,
                # Indicateur principal : chaque compte pese pour 1.
                "Mediane sectorielle (comptes)": round(median_of_medians, 1),
                "Moyenne sectorielle (comptes)": round(mean_of_medians, 1),
                "Dispersion inter-comptes": round(dispersion, 1)
                if not np.isnan(dispersion)
                else None,
                # Indicateurs secondaires : ponderes par le volume de publications.
                "P50 poole": round(float(values.quantile(q1)), 1),
                "P70 poole": round(float(values.quantile(q2)), 1),
                "P80 poole": round(float(values.quantile(q3)), 1),
                "Mediane poolee": round(float(values.median()), 1),
                "Moyenne poolee": round(float(values.mean()), 1),
                "Engagement median (%)": round(float(engagement.median()), 2)
                if not engagement.empty
                else None,
                "Fiabilite": INSUFFICIENT if reasons else RELIABLE,
                "Motif": " ; ".join(reasons),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values(["Secteur", "Plateforme", "Format"]).reset_index(drop=True)

    n_reliable = int((result["Fiabilite"] == RELIABLE).sum())
    logger.info(
        "Benchmark sectoriel : %d combinaison(s), dont %d exploitable(s).",
        len(result),
        n_reliable,
    )
    return result


def compare_accounts_to_sector(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame,
    settings: Settings,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Positionne chaque compte face au benchmark de son secteur.

    Repond a : "comment ce compte se situe-t-il par rapport aux comptes
    similaires ?" La comparaison n'est affichee comme concluante que si le
    benchmark lui-meme est exploitable.
    """
    if frame.empty or benchmark.empty:
        return pd.DataFrame()

    indexed = benchmark.set_index(["Secteur", "Plateforme", "Format"])
    rows: list[dict[str, object]] = []

    for keys, group in frame.groupby(["account_name", "sector", "platform", "format"], dropna=False):
        account, sector, platform, fmt = keys
        if fmt in settings.excluded_formats or (sector, platform, fmt) not in indexed.index:
            continue

        values = pd.to_numeric(group[value_column], errors="coerce").dropna()
        if values.empty:
            continue

        reference = indexed.loc[(sector, platform, fmt)]
        sector_median = reference["Mediane sectorielle (comptes)"]
        account_median = float(values.median())

        if pd.isna(sector_median) or sector_median == 0:
            gap = float("nan")
            position = "Non comparable"
        else:
            gap = round(100.0 * (account_median - sector_median) / sector_median, 1)
            if gap >= 15:
                position = "Au-dessus du secteur"
            elif gap <= -15:
                position = "En dessous du secteur"
            else:
                position = "Dans la norme du secteur"

        unreliable = reference["Fiabilite"] == INSUFFICIENT
        rows.append(
            {
                "Compte": account,
                "Secteur": sector,
                "Plateforme": platform,
                "Format": fmt,
                "Publications": int(values.size),
                "Mediane compte": round(account_median, 1),
                "Mediane secteur": sector_median,
                "Ecart (%)": gap,
                "Positionnement": "Indicatif seulement" if unreliable else position,
                "Fiabilite benchmark": reference["Fiabilite"],
                "Motif": reference["Motif"],
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["Secteur", "Format", "Ecart (%)"], ascending=[True, True, False])
    return result.reset_index(drop=True)

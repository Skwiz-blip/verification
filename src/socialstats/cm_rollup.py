"""Agregation optionnelle des resultats au niveau Community Manager.

Les exports Meta ne contiennent aucune identite de Community Manager : le
perimetre d'evaluation par defaut est donc le COMPTE. Ce module ne produit
quelque chose que si la colonne `cm_name` de config/clients.csv est renseignee.

Regle d'agregation : chaque compte gere par un CM pese pour 1. Un CM en charge
d'un gros compte et d'un petit ne doit pas etre juge principalement sur le gros.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings

logger = logging.getLogger(__name__)


def has_cm_mapping(frame: pd.DataFrame) -> bool:
    """Indique si au moins un compte est rattache a un Community Manager."""
    if "cm_name" not in frame.columns:
        return False
    return bool(frame["cm_name"].astype(str).str.strip().ne("").any())


def rollup_by_cm(
    frame: pd.DataFrame,
    settings: Settings,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Synthetise les performances par Community Manager.

    Retourne un DataFrame vide si aucun rattachement n'est declare.
    """
    if frame.empty or not has_cm_mapping(frame):
        logger.info(
            "Aucun Community Manager declare dans clients.csv : "
            "analyse conservee au niveau compte."
        )
        return pd.DataFrame()

    assigned = frame[frame["cm_name"].astype(str).str.strip() != ""].copy()

    per_account = (
        assigned.assign(_v=pd.to_numeric(assigned[value_column], errors="coerce"))
        .dropna(subset=["_v"])
        .groupby(["cm_name", "account_name"])
        .agg(
            publications=("_v", "size"),
            mediane=("_v", "median"),
            engagement=("engagement_rate", "median"),
        )
        .reset_index()
    )

    rows: list[dict[str, object]] = []
    for cm, group in per_account.groupby("cm_name"):
        total_publications = int(group["publications"].sum())
        rows.append(
            {
                "Community Manager": cm,
                "Comptes geres": int(group["account_name"].nunique()),
                "Publications totales": total_publications,
                # Chaque compte pese pour 1 : mediane des medianes de comptes.
                "Mediane des comptes": round(float(group["mediane"].median()), 1),
                "Engagement median (%)": round(float(group["engagement"].median()), 2)
                if group["engagement"].notna().any()
                else None,
                "Comptes": ", ".join(sorted(group["account_name"].unique())),
                "Fiabilite": settings.reliability.label(total_publications),
            }
        )

    result = pd.DataFrame(rows).sort_values("Publications totales", ascending=False)
    logger.info("Agregation par Community Manager : %d CM identifie(s).", len(result))
    return result.reset_index(drop=True)

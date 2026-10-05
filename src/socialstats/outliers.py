"""Detection des valeurs atypiques et statistiques descriptives robustes.

Principe directeur : DETECTER n'est pas SUPPRIMER.

Sur les reseaux sociaux, une publication virale est une performance reelle, pas
une erreur de mesure. Ce module ne retire donc aucune observation du calcul des
objectifs : il signale les publications exceptionnelles pour le rapport, et
fournit des indicateurs robustes (mediane, moyenne tronquee, MAD) a cote de la
moyenne classique, afin de rendre visible l'ecart entre les deux.

Comparaison des methodes envisagees :
  - Moyenne classique : tres sensible a une seule publication virale. Conservee
    a titre informatif uniquement.
  - Mediane : insensible aux extremes, mais ignore l'amplitude des succes.
  - Moyenne tronquee (10 %) : compromis, mais le taux de troncature est arbitraire.
  - IQR (Tukey) : lisible, standard, adapte aux distributions asymetriques
    typiques des vues. Retenue par defaut pour le SIGNALEMENT.
  - MAD : plus robuste que l'IQR sur petits echantillons, mais degenere quand
    plus de la moitie des valeurs sont identiques (MAD = 0). Proposee en option.

Les objectifs eux-memes sont bases sur des quantiles (voir objectives.py), qui
sont robustes par construction : aucune suppression d'outlier n'est necessaire.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MAD_TO_SIGMA = 1.4826  # Facteur de coherence pour une distribution normale.


@dataclass
class DistributionStats:
    """Portrait statistique complet d'une serie de valeurs."""

    n: int
    mean: float
    median: float
    trimmed_mean_10: float
    std: float
    minimum: float
    maximum: float
    q1: float
    q3: float
    iqr: float
    mad: float
    cv: float
    skewness: float
    n_outliers: int
    n_outliers_high: int
    n_outliers_low: int
    lower_bound: float
    upper_bound: float
    method: str

    def as_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def _empty_stats(method: str) -> DistributionStats:
    nan = float("nan")
    return DistributionStats(
        n=0, mean=nan, median=nan, trimmed_mean_10=nan, std=nan, minimum=nan,
        maximum=nan, q1=nan, q3=nan, iqr=nan, mad=nan, cv=nan, skewness=nan,
        n_outliers=0, n_outliers_high=0, n_outliers_low=0,
        lower_bound=nan, upper_bound=nan, method=method,
    )


def trimmed_mean(values: np.ndarray, proportion: float = 0.10) -> float:
    """Moyenne calculee apres retrait des `proportion` extremes de chaque cote."""
    if values.size == 0:
        return float("nan")
    k = int(np.floor(values.size * proportion))
    if k == 0 or values.size - 2 * k <= 0:
        return float(np.mean(values))
    ordered = np.sort(values)
    return float(np.mean(ordered[k : values.size - k]))


def iqr_bounds(values: np.ndarray, multiplier: float = 1.5) -> tuple[float, float]:
    """Bornes de Tukey. La borne basse est plancheee a 0 (pas de vues negatives)."""
    q1 = float(np.percentile(values, 25))
    q3 = float(np.percentile(values, 75))
    spread = q3 - q1
    return max(0.0, q1 - multiplier * spread), q3 + multiplier * spread


def mad_bounds(values: np.ndarray, threshold: float = 3.5) -> tuple[float, float]:
    """Bornes basees sur l'ecart absolu median.

    Si le MAD vaut 0 (plus de la moitie des valeurs identiques), la methode ne
    discrimine plus rien : on retourne des bornes infinies plutot que de
    declarer aberrante toute valeur differente de la mediane.
    """
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        return float("-inf"), float("inf")
    delta = threshold * mad * MAD_TO_SIGMA
    return max(0.0, median - delta), median + delta


def describe(
    series: pd.Series, method: str = "iqr", multiplier: float = 1.5, mad_threshold: float = 3.5
) -> DistributionStats:
    """Calcule le portrait statistique complet d'une serie, outliers signales."""
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        return _empty_stats(method)

    if method == "mad":
        lower, upper = mad_bounds(values, mad_threshold)
    else:
        lower, upper = iqr_bounds(values, multiplier)

    high = values > upper
    low = values < lower
    q1 = float(np.percentile(values, 25))
    q3 = float(np.percentile(values, 75))
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    median = float(np.median(values))

    return DistributionStats(
        n=int(values.size),
        mean=mean,
        median=median,
        trimmed_mean_10=trimmed_mean(values),
        std=std,
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        q1=q1,
        q3=q3,
        iqr=q3 - q1,
        mad=float(np.median(np.abs(values - median))),
        cv=float(std / mean) if mean else float("nan"),
        skewness=float(pd.Series(values).skew()) if values.size > 2 else float("nan"),
        n_outliers=int(high.sum() + low.sum()),
        n_outliers_high=int(high.sum()),
        n_outliers_low=int(low.sum()),
        lower_bound=lower,
        upper_bound=upper,
        method=method,
    )


def flag_outliers(
    frame: pd.DataFrame,
    value_column: str,
    group_columns: list[str],
    method: str = "iqr",
    multiplier: float = 1.5,
    mad_threshold: float = 3.5,
) -> pd.DataFrame:
    """Marque les publications atypiques AU SEIN de leur groupe de comparaison.

    Les bornes sont calculees par groupe (compte x plateforme x format) : une
    photo n'est pas atypique parce qu'elle fait moins de vues qu'un Reel.
    Aucune ligne n'est supprimee, seules des colonnes de signalement sont ajoutees.
    """
    out = frame.copy()
    out["is_outlier"] = False
    out["outlier_side"] = ""

    for _, index in out.groupby(group_columns, dropna=False).groups.items():
        values = pd.to_numeric(out.loc[index, value_column], errors="coerce")
        clean = values.dropna().to_numpy(dtype=float)
        if clean.size < 4:
            # En dessous de 4 observations, les quartiles ne veulent rien dire.
            continue
        lower, upper = (
            mad_bounds(clean, mad_threshold) if method == "mad" else iqr_bounds(clean, multiplier)
        )
        high = values > upper
        low = values < lower
        out.loc[index, "is_outlier"] = (high | low).fillna(False)
        out.loc[index[high.fillna(False)], "outlier_side"] = "exceptionnellement haute"
        out.loc[index[low.fillna(False)], "outlier_side"] = "exceptionnellement basse"

    n = int(out["is_outlier"].sum())
    logger.info(
        "Outliers signales (methode=%s) : %d publication(s) sur %d, aucune supprimee.",
        method,
        n,
        len(out),
    )
    return out


def compare_methods(series: pd.Series, multiplier: float = 1.5, mad_threshold: float = 3.5) -> pd.DataFrame:
    """Compare les estimateurs de tendance centrale et les methodes de detection.

    Sert a justifier le choix de methode dans le rapport plutot qu'a l'imposer.
    """
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        return pd.DataFrame()

    iqr_low, iqr_high = iqr_bounds(values, multiplier)
    mad_low, mad_high = mad_bounds(values, mad_threshold)
    mean = float(np.mean(values))
    median = float(np.median(values))

    return pd.DataFrame(
        [
            {
                "methode": "Moyenne classique",
                "valeur_centrale": round(mean, 1),
                "outliers_detectes": 0,
                "commentaire": "Sensible aux publications virales",
            },
            {
                "methode": "Mediane",
                "valeur_centrale": round(median, 1),
                "outliers_detectes": 0,
                "commentaire": "Robuste, ignore l'amplitude des succes",
            },
            {
                "methode": "Moyenne tronquee 10%",
                "valeur_centrale": round(trimmed_mean(values), 1),
                "outliers_detectes": int(2 * np.floor(values.size * 0.10)),
                "commentaire": "Compromis, taux de troncature arbitraire",
            },
            {
                "methode": f"IQR (x{multiplier})",
                "valeur_centrale": round(median, 1),
                "outliers_detectes": int(((values < iqr_low) | (values > iqr_high)).sum()),
                "commentaire": f"Bornes [{iqr_low:.0f} ; {iqr_high:.0f}]",
            },
            {
                "methode": f"MAD (seuil {mad_threshold})",
                "valeur_centrale": round(median, 1),
                "outliers_detectes": int(((values < mad_low) | (values > mad_high)).sum()),
                "commentaire": "Degenere si MAD = 0"
                if np.median(np.abs(values - median)) == 0
                else f"Bornes [{mad_low:.0f} ; {mad_high:.0f}]",
            },
        ]
    )

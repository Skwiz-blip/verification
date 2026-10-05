"""Construction des objectifs de prime a trois niveaux.

Methode retenue : quantiles empiriques de la distribution historique du groupe
(compte x plateforme x format), calcules sur les publications organiques.

Pourquoi les quantiles plutot qu'une moyenne + ecart-type :
  1. Ils traduisent DIRECTEMENT la regle metier demandee. "Niveau 1 atteignable
     par environ 50 % des publications" est, par definition, le 50e percentile.
     Une approche moyenne +/- k*ecart-type n'offrirait pas cette garantie sur
     des distributions asymetriques comme les vues.
  2. Ils sont robustes par construction : une publication virale deplace la
     moyenne mais quasiment pas la mediane. Aucune suppression d'outlier n'est
     donc necessaire, ce qui evite d'avoir a decider arbitrairement qu'un vrai
     succes est une "erreur".
  3. Ils restent interpretables par la direction : "ce seuil a ete atteint par
     X % des publications de ce compte l'an dernier".

Interpolation : methode "lower" (on retient une valeur reellement observee).
Un seuil de prime doit correspondre a une performance deja realisee, pas a une
interpolation entre deux publications.

Garde-fou sur les petits echantillons : en dessous de `min_observations`, aucun
seuil chiffre n'est produit. Sur 8 publications, un P80 repose sur 1 ou 2 points
et donnerait une precision illusoire.
"""

from __future__ import annotations

import logging
import math

import pandas as pd

from .config import Settings
from .descriptive import GROUP_COLUMNS

logger = logging.getLogger(__name__)

INSUFFICIENT = "Insuffisante"


def round_threshold(value: float, rounding: int) -> float:
    """Arrondit un seuil au multiple configure, vers le haut.

    Un objectif de prime doit etre un nombre communicable ("80 vues"), pas
    "78,4 vues". L'arrondi vers le haut evite d'annoncer un seuil plus facile
    que celui reellement calcule.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return float("nan")
    if rounding <= 1:
        return float(math.ceil(value))
    return float(math.ceil(value / rounding) * rounding)


def attainment_rate(series: pd.Series, threshold: float) -> float:
    """Part des publications historiques atteignant le seuil (verification a posteriori)."""
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty or math.isnan(threshold):
        return float("nan")
    return round(100.0 * float((values >= threshold).mean()), 1)


def build_objectives(
    frame: pd.DataFrame,
    settings: Settings,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Produit la table des objectifs par compte x plateforme x format."""
    if frame.empty:
        return pd.DataFrame()

    q1, q2, q3 = settings.objectives.quantiles
    rounding = settings.objectives.rounding
    rows: list[dict[str, object]] = []

    for keys, group in frame.groupby(GROUP_COLUMNS, dropna=False):
        account, platform, fmt = keys
        if fmt in settings.excluded_formats:
            continue

        values = pd.to_numeric(group[value_column], errors="coerce").dropna()
        n = int(values.size)
        reliability = settings.reliability.label(n)
        sufficient = n >= settings.min_observations

        row: dict[str, object] = {
            "Compte": account,
            "Secteur": group["sector"].iloc[0] if "sector" in group.columns else "",
            "Plateforme": platform,
            "Format": fmt,
            "Publications": n,
            "Mediane observee": round(float(values.median()), 1) if n else float("nan"),
            "Fiabilite": reliability,
        }

        if not sufficient or n == 0:
            row.update(
                {
                    "Niveau 1": None,
                    "Niveau 2": None,
                    "Niveau 3": None,
                    "Engagement cible (%)": None,
                    "Atteinte N1 (%)": None,
                    "Atteinte N2 (%)": None,
                    "Atteinte N3 (%)": None,
                    "Dispersion N3/N1": None,
                    "Statut": f"Echantillon insuffisant ({n} < {settings.min_observations})",
                    "Avertissement": (
                        "Aucun seuil chiffre : statistiques descriptives uniquement. "
                        "Accumulez davantage de publications avant de fixer un objectif."
                    ),
                }
            )
            rows.append(row)
            continue

        level_1 = round_threshold(float(values.quantile(q1, interpolation="lower")), rounding)
        level_2 = round_threshold(float(values.quantile(q2, interpolation="lower")), rounding)
        level_3 = round_threshold(float(values.quantile(q3, interpolation="lower")), rounding)

        # L'arrondi peut aplatir deux niveaux voisins sur une distribution
        # resserree : on garantit une progression stricte entre les paliers.
        level_2 = max(level_2, level_1 + rounding)
        level_3 = max(level_3, level_2 + rounding)

        engagement = pd.to_numeric(group["engagement_rate"], errors="coerce").dropna()
        engagement_target = round(float(engagement.quantile(q1)), 2) if not engagement.empty else None

        warnings: list[str] = []
        if reliability not in {"Moyenne", "Elevee"}:
            warnings.append("Seuils indicatifs : echantillon limite, a reviser au prochain cycle.")

        # Distribution "loterie" : quelques publications virales tirent le palier
        # haut tres au-dessus du quotidien du compte. Le seuil reste
        # mathematiquement correct, mais viser N3 devient un pari, pas un
        # objectif de travail. Mieux vaut le signaler que le presenter comme
        # une cible atteignable par l'effort.
        if level_1 > 0 and level_3 / level_1 > settings.objectives.max_level_spread:
            warnings.append(
                f"Distribution tres dispersee (N3 = {level_3 / level_1:.1f} x N1) : "
                "le palier 3 depend de publications exceptionnelles et ne constitue "
                "pas un objectif de travail realiste."
            )

        row.update(
            {
                "Niveau 1": level_1,
                "Niveau 2": level_2,
                "Niveau 3": level_3,
                "Engagement cible (%)": engagement_target,
                "Atteinte N1 (%)": attainment_rate(values, level_1),
                "Atteinte N2 (%)": attainment_rate(values, level_2),
                "Atteinte N3 (%)": attainment_rate(values, level_3),
                "Dispersion N3/N1": round(level_3 / level_1, 1) if level_1 else None,
                "Statut": "Objectifs calcules",
                "Avertissement": " ".join(warnings),
            }
        )
        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values(["Compte", "Plateforme", "Format"]).reset_index(drop=True)

    n_ok = int((result["Statut"] == "Objectifs calcules").sum())
    logger.info(
        "Objectifs : %d groupe(s) avec seuils chiffres, %d en echantillon insuffisant.",
        n_ok,
        len(result) - n_ok,
    )
    return result


def evaluate_against_objectives(
    frame: pd.DataFrame, objectives: pd.DataFrame, recent_n: int = 10
) -> pd.DataFrame:
    """Compare les publications recentes d'un compte a ses propres objectifs.

    Repond a la question : "le Community Manager performe-t-il mieux que
    l'historique habituel de ce compte ?"
    """
    if frame.empty or objectives.empty:
        return pd.DataFrame()

    usable = objectives[objectives["Statut"] == "Objectifs calcules"]
    rows: list[dict[str, object]] = []

    for _, obj in usable.iterrows():
        group = frame[
            (frame["account_name"] == obj["Compte"])
            & (frame["platform"] == obj["Plateforme"])
            & (frame["format"] == obj["Format"])
        ].sort_values("published_at")
        if group.empty:
            continue

        recent = group.tail(recent_n)
        values = pd.to_numeric(recent["views_organic_resolved"], errors="coerce").dropna()
        if values.empty:
            continue

        median_recent = float(values.median())
        if median_recent >= obj["Niveau 3"]:
            level = "Niveau 3 atteint"
        elif median_recent >= obj["Niveau 2"]:
            level = "Niveau 2 atteint"
        elif median_recent >= obj["Niveau 1"]:
            level = "Niveau 1 atteint"
        else:
            level = "Sous le Niveau 1"

        rows.append(
            {
                "Compte": obj["Compte"],
                "Secteur": obj["Secteur"],
                "Plateforme": obj["Plateforme"],
                "Format": obj["Format"],
                f"Publications recentes (max {recent_n})": int(values.size),
                "Mediane recente": round(median_recent, 1),
                "Niveau 1": obj["Niveau 1"],
                "Niveau 2": obj["Niveau 2"],
                "Niveau 3": obj["Niveau 3"],
                "Niveau atteint": level,
                "Ecart au Niveau 1 (%)": round(
                    100.0 * (median_recent - obj["Niveau 1"]) / obj["Niveau 1"], 1
                )
                if obj["Niveau 1"]
                else float("nan"),
            }
        )

    return pd.DataFrame(rows)


def build_sector_objectives(
    frame: pd.DataFrame, settings: Settings, value_column: str = "views_organic_resolved"
) -> pd.DataFrame:
    """Objectifs de prime au niveau SECTEUR x PLATEFORME x FORMAT.

    Pourquoi ne pas reutiliser les quantiles pooles du benchmark
    ---------------------------------------------------------------------
    Mettre toutes les publications du secteur dans un seul sac donne un poids
    proportionnel au volume de publication. Sur les donnees reelles, le secteur
    Mode/Lifestyle sur Instagram/Photo ressort a 383 vues en poole contre 109
    en mediane par compte : l'ecart vient d'un seul compte qui publie 342 photos
    tres exposees. Fixer 383 comme objectif de categorie condamnerait les six
    autres comptes a l'echec pour une raison qui ne tient pas a leur travail.

    Methode retenue : chaque compte qualifie calcule SES propres paliers, puis
    on prend la MEDIANE de ces paliers entre comptes. Chaque compte pese 1,
    quel que soit son volume. C'est la transposition directe de la "mediane des
    medianes" deja retenue pour le benchmark.

    Ces objectifs sectoriels servent de reference de categorie ; ils ne
    remplacent pas les objectifs par compte, qui restent la base d'evaluation
    d'un Community Manager sur son propre historique.
    """
    required = {"sector", "platform", "format", value_column}
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"Colonnes manquantes pour les objectifs sectoriels : {sorted(missing)}")

    q1, q2, q3 = settings.objectives.quantiles
    rounding = settings.objectives.rounding
    min_per_account = settings.sector.min_observations_per_account
    excluded = set(settings.excluded_formats)

    rows: list[dict] = []
    grouped = frame.groupby(["sector", "platform", "format"], dropna=False)

    for (sector, platform, fmt), group in grouped:
        if fmt in excluded:
            continue

        values = pd.to_numeric(group[value_column], errors="coerce").dropna()
        row = {
            "Secteur": sector,
            "Plateforme": platform,
            "Format": fmt,
            "Publications": int(values.shape[0]),
        }

        # Un compte ne participe qu'avec un historique suffisant : sinon ses
        # paliers seraient du bruit, et ce bruit pese autant que les autres
        # dans une mediane inter-comptes.
        per_account: list[dict] = []
        for account, sub in group.groupby("account_name", dropna=False):
            account_values = pd.to_numeric(sub[value_column], errors="coerce").dropna()
            if account_values.shape[0] < min_per_account:
                continue
            per_account.append(
                {
                    "compte": account,
                    "n": int(account_values.shape[0]),
                    "n1": float(account_values.quantile(q1, interpolation="lower")),
                    "n2": float(account_values.quantile(q2, interpolation="lower")),
                    "n3": float(account_values.quantile(q3, interpolation="lower")),
                }
            )

        n_accounts = len(per_account)
        row["Comptes qualifies"] = n_accounts

        required = settings.sector.required_accounts(sector)
        if n_accounts < required or values.shape[0] < settings.sector.min_publications:
            row.update(
                {
                    "Niveau 1": None,
                    "Niveau 2": None,
                    "Niveau 3": None,
                    "Dispersion inter-comptes N1": None,
                    "Statut": "Echantillon sectoriel insuffisant",
                    "Avertissement": (
                        f"{n_accounts} compte(s) qualifie(s) et {values.shape[0]} publication(s) : "
                        f"minimum requis {required} comptes et "
                        f"{settings.sector.min_publications} publications."
                    ),
                }
            )
            rows.append(row)
            continue

        per_account_df = pd.DataFrame(per_account)
        level_1 = round_threshold(float(per_account_df["n1"].median()), rounding)
        level_2 = round_threshold(float(per_account_df["n2"].median()), rounding)
        level_3 = round_threshold(float(per_account_df["n3"].median()), rounding)

        # Les paliers doivent rester strictement croissants meme si la mediane
        # inter-comptes les rapproche apres arrondi.
        level_2 = max(level_2, level_1 + rounding)
        level_3 = max(level_3, level_2 + rounding)

        warnings: list[str] = []
        if settings.sector.is_forced(sector):
            warnings.append(
                f"Derogation : categorie calculee sur {n_accounts} compte(s) seulement "
                f"(minimum general : {settings.sector.min_accounts}). Les paliers decrivent "
                "ces comptes, pas un marche."
            )
        # Dispersion entre comptes : si les paliers individuels varient
        # enormement, la "categorie" ne decrit pas un marche homogene et une
        # cible commune serait arbitraire.
        spread = float("nan")
        if per_account_df["n1"].median() > 0:
            spread = float(per_account_df["n1"].max() / max(per_account_df["n1"].min(), 1))
            if spread > settings.objectives.max_level_spread:
                warnings.append(
                    f"Comptes tres heterogenes (rapport {spread:.1f} x entre le plus fort "
                    "et le plus faible) : une cible unique de categorie a peu de sens ici, "
                    "privilegier les objectifs par compte."
                )

        row.update(
            {
                "Niveau 1": level_1,
                "Niveau 2": level_2,
                "Niveau 3": level_3,
                "Dispersion inter-comptes N1": round(spread, 1) if spread == spread else None,
                "Atteinte N1 (%)": attainment_rate(values, level_1),
                "Atteinte N2 (%)": attainment_rate(values, level_2),
                "Atteinte N3 (%)": attainment_rate(values, level_3),
                "Statut": "Objectifs calcules",
                "Avertissement": " ".join(warnings),
            }
        )
        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    computed = int((result["Statut"] == "Objectifs calcules").sum())
    logger.info(
        "Objectifs sectoriels : %d categorie(s) avec seuils chiffres, %d insuffisante(s).",
        computed,
        len(result) - computed,
    )
    return result.sort_values(["Secteur", "Plateforme", "Format"]).reset_index(drop=True)

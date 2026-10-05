"""Controle mensuel : les publications d'un mois face aux objectifs d'une reference.

Le rapport annuel calcule les seuils et les verifie sur les memes publications.
Le controle mensuel separe les deux : les objectifs sont calcules sur une
PERIODE DE REFERENCE, puis chaque mois est confronte a ces seuils. Charger un
nouvel export ne deplace donc pas la cible du mois que l'on juge, tant que ce
mois reste hors de la reference.

Regle de verdict, identique a `objectives.evaluate_against_objectives` : la
MEDIANE des publications du mois est comparee aux trois paliers. Un mois trop
peu alimente ne recoit aucun verdict, pour la meme raison qu'un petit
echantillon ne recoit aucun seuil.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import Settings
from .descriptive import GROUP_COLUMNS

logger = logging.getLogger(__name__)

OBJECTIVE_KEYS = ["Compte", "Plateforme", "Format"]
CATEGORY_KEYS = ["Secteur", "Plateforme", "Format"]
LEVELS = ["Niveau 1", "Niveau 2", "Niveau 3"]
ATTAINMENT = ["Atteinte N1 (%)", "Atteinte N2 (%)", "Atteinte N3 (%)"]
COMPUTED = "Objectifs calcules"

LEVEL_3 = "Niveau 3 atteint"
LEVEL_2 = "Niveau 2 atteint"
LEVEL_1 = "Niveau 1 atteint"
BELOW = "Sous le Niveau 1"
TOO_FEW = "Trop peu de publications"
TOO_FEW_ACCOUNTS = "Trop peu de comptes"
NO_OBJECTIVE = "Pas d'objectif"
NO_POST = "Aucune publication"

REACHED = (LEVEL_1, LEVEL_2, LEVEL_3)
EVALUATED = (BELOW, *REACHED)

# Periodes de reference proposees pour le calcul des objectifs.
REFERENCE_BEFORE = "before"
REFERENCE_6 = "6"
REFERENCE_3 = "3"
REFERENCE_ALL = "all"
REFERENCE_CUSTOM = "custom"
REFERENCE_PRESETS = (REFERENCE_BEFORE, REFERENCE_6, REFERENCE_3, REFERENCE_ALL, REFERENCE_CUSTOM)

RESULT_COLUMNS = [
    "Compte",
    "Client",
    "Secteur",
    "Plateforme",
    "Format",
    "Mois",
    "Publications",
    "Vues du mois",
    "Mediane du mois",
    *LEVELS,
    *ATTAINMENT,
    "Ecart au Niveau 1 (%)",
    "Niveau atteint",
]


def available_months(frame: pd.DataFrame) -> list[pd.Period]:
    """Mois presents dans le jeu de donnees, du plus ancien au plus recent."""
    if frame.empty:
        return []
    return sorted(frame["published_at"].dropna().dt.to_period("M").unique())


def default_month(frame: pd.DataFrame, min_share: float = 0.2) -> pd.Period | None:
    """Dernier mois suffisamment alimente pour etre controle.

    Un export "du 1er aout au 1er aout" laisse quelques publications isolees
    dans un treizieme mois : le proposer par defaut afficherait un controle
    vide. On retient le dernier mois qui pese au moins `min_share` du volume
    mensuel median.
    """
    months = frame["published_at"].dropna().dt.to_period("M")
    if months.empty:
        return None
    counts = months.value_counts().sort_index()
    filled = counts[counts >= min_share * counts.median()]
    return (filled if not filled.empty else counts).index[-1]


def slice_months(frame: pd.DataFrame, start: pd.Period, end: pd.Period) -> pd.DataFrame:
    """Publications dont le mois est compris entre `start` et `end` inclus."""
    months = frame["published_at"].dt.to_period("M")
    return frame[(months >= start) & (months <= end)]


def reference_bounds(
    preset: str,
    months: list[pd.Period],
    month: pd.Period,
    start: pd.Period | None = None,
    end: pd.Period | None = None,
) -> tuple[pd.Period, pd.Period] | None:
    """Bornes de la periode de reference ; None s'il n'existe aucun mois anterieur.

    Par defaut la reference s'arrete AVANT le mois controle : ses publications
    ne doivent pas servir a fixer les seuils auxquels elles sont comparees. Les
    bornes sont toujours des mois reellement charges, un mois sans publication
    pouvant manquer au milieu de l'historique.
    """
    if preset == REFERENCE_ALL:
        return months[0], months[-1]
    if preset == REFERENCE_CUSTOM and start is not None and end is not None:
        low, high = sorted((start, end))
        inside = [m for m in months if low <= m <= high]
        if inside:
            return inside[0], inside[-1]

    earlier = [m for m in months if m < month]
    if not earlier:
        return None
    if preset in (REFERENCE_6, REFERENCE_3):
        window = [m for m in earlier if m >= month - int(preset)]
        return (window or earlier[-1:])[0], earlier[-1]
    return earlier[0], earlier[-1]


def _thresholds(objectives: pd.DataFrame) -> pd.DataFrame:
    """Seuils numeriques par groupe ; NaN quand aucun objectif n'a ete calcule."""
    if objectives.empty:
        return pd.DataFrame(
            {
                **{column: pd.Series(dtype=object) for column in GROUP_COLUMNS},
                **{level: pd.Series(dtype="float64") for level in LEVELS},
            }
        )
    out = objectives[OBJECTIVE_KEYS + LEVELS].copy()
    out[LEVELS] = out[LEVELS].apply(pd.to_numeric, errors="coerce").astype("float64")
    return out.rename(columns=dict(zip(OBJECTIVE_KEYS, GROUP_COLUMNS)))


def _level_reached(values: pd.Series, table: pd.DataFrame) -> np.ndarray:
    """Palier atteint par `values` face aux seuils de chaque ligne de `table`."""
    return np.select(
        [
            table["Niveau 1"].isna(),
            values >= table["Niveau 3"],
            values >= table["Niveau 2"],
            values >= table["Niveau 1"],
        ],
        [NO_OBJECTIVE, LEVEL_3, LEVEL_2, LEVEL_1],
        default=BELOW,
    )


def monthly_results(
    frame: pd.DataFrame,
    objectives: pd.DataFrame,
    settings: Settings,
    min_posts: int = 3,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Resultats de chaque compte x plateforme x format, mois par mois.

    `objectives` est la table produite par `build_objectives` sur la periode
    de reference ; `frame` couvre tous les mois a controler.
    """
    if frame.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    data = frame[~frame["format"].isin(settings.excluded_formats)].copy()
    # Les vues resolues sont en Float64 nullable : on repasse en float64 pour
    # que les comparaisons aux seuils donnent des booleens ordinaires.
    data["_v"] = pd.to_numeric(data[value_column], errors="coerce").astype("float64")
    data = data.dropna(subset=["_v", "published_at"])
    if data.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    data["Mois"] = data["published_at"].dt.to_period("M")
    data["Secteur"] = data["sector"] if "sector" in data.columns else ""
    # A defaut de declaration dans clients.csv, le compte fait office de client.
    data["Client"] = data["client"] if "client" in data.columns else data["account_name"]
    data = data.merge(_thresholds(objectives), on=GROUP_COLUMNS, how="left")

    for label, level in zip(ATTAINMENT, LEVELS):
        hit = (data["_v"] >= data[level]).astype("float64")
        data[label] = hit.where(data[level].notna())

    out = (
        data.groupby(GROUP_COLUMNS + ["Mois"], dropna=False)
        .agg(
            **{
                "Client": ("Client", "first"),
                "Secteur": ("Secteur", "first"),
                "Publications": ("_v", "size"),
                "Vues du mois": ("_v", "sum"),
                "Mediane du mois": ("_v", "median"),
                **{level: (level, "first") for level in LEVELS},
                **{label: (label, "mean") for label in ATTAINMENT},
            }
        )
        .reset_index()
        .rename(columns=dict(zip(GROUP_COLUMNS, OBJECTIVE_KEYS)))
    )

    out["Mediane du mois"] = out["Mediane du mois"].round(1)
    out[ATTAINMENT] = (100.0 * out[ATTAINMENT]).round(1)
    level_1 = out["Niveau 1"].where(out["Niveau 1"] > 0)
    out["Ecart au Niveau 1 (%)"] = (100.0 * (out["Mediane du mois"] - level_1) / level_1).round(1)

    verdict = _level_reached(out["Mediane du mois"], out)
    too_few = out["Niveau 1"].notna() & (out["Publications"] < min_posts)
    out["Niveau atteint"] = np.where(too_few, TOO_FEW, verdict)

    out = out[RESULT_COLUMNS].sort_values(OBJECTIVE_KEYS + ["Mois"]).reset_index(drop=True)
    logger.info(
        "Controle mensuel : %d ligne(s) compte x format x mois, %d evaluee(s).",
        len(out),
        int(out["Niveau atteint"].isin(EVALUATED).sum()),
    )
    return out


def month_view(results: pd.DataFrame, objectives: pd.DataFrame, month: pd.Period) -> pd.DataFrame:
    """Resultats d'un mois, completes des groupes dotes d'objectifs restes muets.

    Un compte qui cesse de publier disparaitrait sinon du controle au moment
    precis ou il faut le remarquer.
    """
    if results.empty:
        return results
    current = results[results["Mois"] == month]
    if objectives.empty:
        return current.reset_index(drop=True)

    expected = objectives.loc[objectives["Statut"] == COMPUTED, OBJECTIVE_KEYS + ["Secteur"] + LEVELS]
    silent = expected.merge(current[OBJECTIVE_KEYS], on=OBJECTIVE_KEYS, how="left", indicator=True)
    silent = silent[silent["_merge"] == "left_only"].drop(columns="_merge")
    if silent.empty:
        return current.reset_index(drop=True)

    clients = results.drop_duplicates("Compte").set_index("Compte")["Client"]
    silent = silent.assign(
        Client=silent["Compte"].map(clients).fillna(silent["Compte"]),
        Mois=month,
        Publications=0,
        **{"Vues du mois": 0.0, "Niveau atteint": NO_POST},
    )
    silent[LEVELS] = silent[LEVELS].apply(pd.to_numeric, errors="coerce").astype("float64")
    for column in results.columns.difference(silent.columns):
        # Meme dtype que la table principale : la concatenation reste stable.
        silent[column] = pd.Series(np.nan, index=silent.index, dtype=results[column].dtype)

    out = pd.concat([current, silent[results.columns]], ignore_index=True)
    return out.sort_values(OBJECTIVE_KEYS).reset_index(drop=True)


def category_results(
    results: pd.DataFrame,
    sector_objectives: pd.DataFrame,
    min_posts: int = 3,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Resultats par categorie x plateforme x format, mois par mois.

    Chaque compte pese pour 1, comme dans le benchmark : la valeur de la
    categorie est la mediane des medianes mensuelles de ses comptes. Un compte
    sous `min_posts` n'y participe pas, sa mediane mensuelle etant du bruit.

    Avec `settings`, la categorie ne recoit un niveau que si le mois compte
    autant de comptes evalues que le benchmark en exige (`sector.min_accounts`,
    derogations comprises) : la "mediane des comptes" d'un seul compte n'est
    que ce compte.

    Deux lectures sont fournies cote a cote, car elles ne repondent pas a la
    meme question : la categorie face a SES paliers communs, et le nombre de
    comptes qui atteignent LEUR propre Niveau 1. Quand la categorie est trop
    heterogene pour porter une cible commune, seule la seconde a du sens.
    """
    if results.empty:
        return pd.DataFrame()

    keys = CATEGORY_KEYS + ["Mois"]
    active = results.groupby(keys, dropna=False).agg(
        **{"Comptes actifs": ("Compte", "nunique"), "Publications": ("Publications", "sum")}
    )
    typical = (
        results[results["Publications"] >= min_posts]
        .groupby(keys, dropna=False)
        .agg(
            **{
                "Comptes evalues": ("Compte", "nunique"),
                "Mediane des comptes": ("Mediane du mois", "median"),
            }
        )
    )
    own = results[results["Niveau atteint"].isin(EVALUATED)]
    own = (
        own.assign(_reached=own["Niveau atteint"].isin(REACHED))
        .groupby(keys, dropna=False)
        .agg(
            **{
                "Comptes avec objectif": ("Compte", "nunique"),
                "Comptes a leur Niveau 1 ou plus": ("_reached", "sum"),
            }
        )
    )

    out = active.join(typical).join(own).reset_index()
    counts = ["Comptes evalues", "Comptes avec objectif", "Comptes a leur Niveau 1 ou plus"]
    out[counts] = out[counts].fillna(0).astype(int)
    out["Mediane des comptes"] = out["Mediane des comptes"].round(1)

    extra = ["Dispersion inter-comptes N1", "Avertissement"]
    if sector_objectives.empty:
        levels = pd.DataFrame(columns=CATEGORY_KEYS + LEVELS + extra)
    else:
        computed = sector_objectives[sector_objectives["Statut"] == COMPUTED]
        levels = computed.reindex(columns=CATEGORY_KEYS + LEVELS + extra)
    out = out.merge(levels, on=CATEGORY_KEYS, how="left")
    numeric = LEVELS + extra[:1]
    out[numeric] = out[numeric].apply(pd.to_numeric, errors="coerce").astype("float64")
    out["Avertissement"] = out["Avertissement"].fillna("")

    if settings is None:
        required = pd.Series(1, index=out.index)
    else:
        required = out["Secteur"].map(settings.sector.required_accounts)
    verdict = _level_reached(out["Mediane des comptes"], out)
    too_few = out["Niveau 1"].notna() & (out["Comptes evalues"] < required)
    out["Niveau atteint"] = np.where(too_few, TOO_FEW_ACCOUNTS, verdict)
    return out.sort_values(keys).reset_index(drop=True)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").astype("float64")


def views_per_reach(
    frame: pd.DataFrame,
    settings: Settings,
    min_posts: int = 15,
    value_column: str = "views_organic_resolved",
) -> pd.DataFrame:
    """Nombre median de vues par personne touchee, par plateforme x format x mois.

    Ce ratio ne depend pas de la taille de l'audience : il n'a pas de raison de
    bouger d'un mois a l'autre. S'il se deplace d'un coup sur une plateforme,
    c'est la facon de compter les vues qui a change, et des seuils en vues
    calcules de part et d'autre ne sont plus comparables.

    La couverture suit la meme cascade que le taux d'engagement : organique si
    elle existe, sinon totale pour une publication non sponsorisee.
    """
    columns = ["Plateforme", "Format", "Mois", "Publications", "Vues par personne touchee"]
    data = frame[~frame["format"].isin(settings.excluded_formats)]
    if data.empty:
        return pd.DataFrame(columns=columns)

    total = _numeric(data, "reach_total")
    if "is_sponsored" in data.columns:
        total = total.where(~data["is_sponsored"].astype(bool))
    reach = _numeric(data, "reach_organic").fillna(total)
    ratio = _numeric(data, value_column) / reach.where(reach > 0)

    table = pd.DataFrame(
        {
            "Plateforme": data["platform"],
            "Format": data["format"],
            "Mois": data["published_at"].dt.to_period("M"),
            "_ratio": ratio,
        }
    ).dropna(subset=["_ratio", "Mois"])
    out = (
        table.groupby(["Plateforme", "Format", "Mois"])
        .agg(**{"Publications": ("_ratio", "size"), "Vues par personne touchee": ("_ratio", "median")})
        .reset_index()
    )
    out["Vues par personne touchee"] = out["Vues par personne touchee"].round(2)
    # Sous ce volume, la mediane mensuelle du ratio est trop bruitee pour conclure.
    return out[out["Publications"] >= min_posts].reset_index(drop=True)


def measurement_breaks(ratios: pd.DataFrame, threshold: float = 1.5) -> pd.DataFrame:
    """Plateformes x formats dont le ratio vues / couverture a change d'echelle.

    `ratios` est la table de `views_per_reach`. Une serie est signalee quand
    son mois le plus haut depasse `threshold` fois son mois le plus bas.
    """
    value = "Vues par personne touchee"
    rows: list[dict[str, object]] = []
    for (platform, fmt), group in ratios.groupby(["Plateforme", "Format"]):
        if len(group) < 2:
            continue
        high = group.loc[group[value].idxmax()]
        low = group.loc[group[value].idxmin()]
        if low[value] <= 0 or high[value] / low[value] < threshold:
            continue
        rows.append(
            {
                "Plateforme": platform,
                "Format": fmt,
                "Mois haut": high["Mois"],
                "Ratio haut": float(high[value]),
                "Mois bas": low["Mois"],
                "Ratio bas": float(low[value]),
                "Rapport": round(float(high[value] / low[value]), 1),
            }
        )
    return pd.DataFrame(rows)

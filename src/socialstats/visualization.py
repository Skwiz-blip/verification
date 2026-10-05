"""Generation des graphiques d'analyse.

Le backend non interactif "Agg" est force : les graphiques sont produits en
fichiers, jamais affiches, ce qui permet l'execution sur un serveur sans ecran.

Choix de lecture : les distributions de vues sont fortement asymetriques. Les
boxplots utilisent donc une echelle logarithmique, sans quoi toutes les boites
seraient ecrasees en bas du graphique par quelques publications virales.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from .config import Settings
from .sector_analysis import INSUFFICIENT

logger = logging.getLogger(__name__)

PALETTE = "deep"
FIGSIZE = (11, 6)


def _setup() -> None:
    sns.set_theme(style="whitegrid", palette=PALETTE)
    plt.rcParams.update({"figure.autolayout": True, "axes.titleweight": "bold"})


def _save(fig: plt.Figure, path: Path, dpi: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    logger.info("Graphique ecrit : %s", path.name)
    return path


def plot_views_distribution(frame: pd.DataFrame, settings: Settings, out_dir: Path) -> Path | None:
    """Distribution des vues organiques par format."""
    usable = frame[~frame["format"].isin(settings.excluded_formats)].dropna(
        subset=["views_organic_resolved"]
    )
    if usable.empty:
        return None

    fig, ax = plt.subplots(figsize=FIGSIZE)
    order = (
        usable.groupby("format")["views_organic_resolved"].median().sort_values(ascending=False).index
    )
    sns.boxplot(data=usable, x="format", y="views_organic_resolved", order=order, ax=ax)
    sns.stripplot(
        data=usable, x="format", y="views_organic_resolved", order=order, ax=ax,
        color="black", alpha=0.25, size=3,
    )
    ax.set_yscale("log")
    ax.set_title("Distribution des vues organiques par format")
    ax.set_xlabel("Format")
    ax.set_ylabel("Vues organiques (echelle log)")
    # Ce graphique agrege toutes les publications : il est mecaniquement domine
    # par les comptes les plus prolifiques. Les tables, elles, ponderent chaque
    # compte a parts egales. Le signaler evite une lecture contradictoire.
    n_accounts = usable["account_name"].nunique()
    ax.text(
        0.5,
        -0.16,
        f"Toutes publications confondues ({n_accounts} comptes de tailles differentes) : "
        "vue ponderee par le volume, a lire avec les tables qui ponderent chaque compte a 1.",
        transform=ax.transAxes,
        ha="center",
        fontsize=8.5,
        color="#555555",
    )
    return _save(fig, out_dir / "01_distribution_vues_par_format.png", settings.output.chart_dpi)


def plot_format_comparison(
    comparison: pd.DataFrame, settings: Settings, out_dir: Path, top_n: int = 12
) -> Path | None:
    """Comparaison des formats, pour les comptes les plus actifs.

    Au-dela d'une douzaine de comptes le graphique devient illisible : il est
    restreint aux plus gros volumes, la table `comparaison_formats.csv`
    conservant l'exhaustivite.
    """
    if comparison.empty:
        return None

    usable = comparison.copy()
    # Un meme compte peut exister sur les deux plateformes : sans la plateforme
    # dans le libelle, seaborn fusionnerait les deux en une barre moyenne.
    if "Platform" in usable.columns:
        usable["libelle"] = usable["Compte"] + " / " + usable["Platform"]
    else:
        usable["libelle"] = usable["Compte"]

    volumes = usable.groupby("libelle")["Publications"].sum().nlargest(top_n)
    usable = usable[usable["libelle"].isin(volumes.index)]
    if usable.empty:
        return None

    fig, ax = plt.subplots(figsize=(11, max(5, 0.45 * usable["libelle"].nunique())))
    sns.barplot(
        data=usable,
        y="libelle",
        x="Vues medianes",
        hue="Format",
        order=volumes.index,
        ax=ax,
    )
    total = comparison["Compte"].nunique()
    shown = usable["libelle"].nunique()
    ax.set_title(
        f"Vues medianes par format ({shown} comptes les plus actifs sur {total})"
    )
    ax.set_xlabel("Vues organiques medianes")
    ax.set_ylabel("")
    ax.legend(title="Format", bbox_to_anchor=(1.02, 1), loc="upper left")
    return _save(fig, out_dir / "02_comparaison_formats.png", settings.output.chart_dpi)


def plot_time_evolution(monthly: pd.DataFrame, settings: Settings, out_dir: Path) -> Path | None:
    """Evolution mensuelle des vues medianes par compte."""
    if monthly.empty:
        return None

    fig, ax = plt.subplots(figsize=FIGSIZE)
    sns.lineplot(data=monthly, x="mois", y="Vues medianes", hue="Compte", marker="o", ax=ax)
    ax.set_title("Evolution des vues organiques medianes")
    ax.set_xlabel("Mois")
    ax.set_ylabel("Vues organiques medianes")
    ax.tick_params(axis="x", rotation=30)
    ax.legend(title="Compte", bbox_to_anchor=(1.02, 1), loc="upper left")
    return _save(fig, out_dir / "03_evolution_temporelle.png", settings.output.chart_dpi)


def plot_sector_comparison(comparison: pd.DataFrame, settings: Settings, out_dir: Path) -> Path | None:
    """Ecart de chaque compte a la mediane de son secteur."""
    if comparison.empty or "Ecart (%)" not in comparison.columns:
        return None

    usable = comparison.dropna(subset=["Ecart (%)"]).copy()
    if usable.empty:
        return None

    # Un benchmark non fiable est signale directement dans le libelle, pour
    # qu'aucun lecteur ne surinterprete un ecart calcule sur trop peu de comptes.
    usable["libelle"] = (
        usable["Compte"] + " / " + usable["Plateforme"] + " / " + usable["Format"]
    )
    unreliable = usable["Fiabilite benchmark"] == INSUFFICIENT
    usable.loc[unreliable, "libelle"] += "  (benchmark insuffisant)"
    usable = usable.sort_values("Ecart (%)")

    fig, ax = plt.subplots(figsize=(11, max(4, 0.35 * len(usable))))
    colors = [
        "#bdbdbd" if flag else ("#2a9d8f" if value >= 0 else "#e76f51")
        for value, flag in zip(usable["Ecart (%)"], usable["Fiabilite benchmark"] == INSUFFICIENT)
    ]
    ax.barh(usable["libelle"], usable["Ecart (%)"], color=colors)
    ax.axvline(0, color="black", linewidth=1)
    ax.set_title("Ecart de chaque compte a la mediane de son secteur")
    ax.set_xlabel("Ecart (%)")
    ax.set_ylabel("")
    return _save(fig, out_dir / "04_comparaison_sectorielle.png", settings.output.chart_dpi)


def plot_objectives(objectives: pd.DataFrame, settings: Settings, out_dir: Path) -> Path | None:
    """Repartition des seuils Niveau 1 / 2 / 3."""
    if objectives.empty:
        return None

    usable = objectives[objectives["Statut"] == "Objectifs calcules"].copy()
    if usable.empty:
        return None

    # La plateforme fait partie du libelle : un meme compte peut exister sur
    # Facebook et Instagram, et seuls les libelles distincts empechent seaborn
    # d'agreger les deux lignes en une barre moyenne assortie d'un intervalle.
    usable["libelle"] = (
        usable["Compte"] + " / " + usable["Plateforme"] + " / " + usable["Format"]
    )
    melted = usable.melt(
        id_vars="libelle",
        value_vars=["Niveau 1", "Niveau 2", "Niveau 3"],
        var_name="Niveau",
        value_name="Vues",
    )

    fig, ax = plt.subplots(figsize=(11, max(4, 0.5 * len(usable))))
    sns.barplot(data=melted, y="libelle", x="Vues", hue="Niveau", ax=ax)
    ax.set_title("Objectifs de prime par palier")
    ax.set_xlabel("Vues organiques requises")
    ax.set_ylabel("")
    ax.legend(title="Palier", bbox_to_anchor=(1.02, 1), loc="upper left")
    return _save(fig, out_dir / "05_objectifs_par_niveau.png", settings.output.chart_dpi)


def generate_all(
    frame: pd.DataFrame,
    format_comparison: pd.DataFrame,
    monthly: pd.DataFrame,
    sector_comparison: pd.DataFrame,
    objectives: pd.DataFrame,
    settings: Settings,
) -> list[Path]:
    """Produit tous les graphiques. Un echec unitaire n'interrompt pas le lot."""
    if not settings.output.charts:
        logger.info("Generation des graphiques desactivee dans la configuration.")
        return []

    _setup()
    out_dir = settings.output_dir / "charts"
    jobs = [
        ("distribution des vues", lambda: plot_views_distribution(frame, settings, out_dir)),
        ("comparaison des formats", lambda: plot_format_comparison(format_comparison, settings, out_dir)),
        ("evolution temporelle", lambda: plot_time_evolution(monthly, settings, out_dir)),
        ("comparaison sectorielle", lambda: plot_sector_comparison(sector_comparison, settings, out_dir)),
        ("objectifs par niveau", lambda: plot_objectives(objectives, settings, out_dir)),
    ]

    produced: list[Path] = []
    for label, job in jobs:
        try:
            path = job()
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Graphique '%s' non genere : %s", label, exc)
            continue
        if path is not None:
            produced.append(path)

    logger.info("%d graphique(s) genere(s).", len(produced))
    return produced

"""Assemblage des sorties : tables CSV/Excel et rapport de recommandations.

Le rapport texte est genere a partir des tables calculees, jamais a partir
d'observations codees en dur : ajouter un compte ou un secteur fait apparaitre
de nouvelles recommandations sans modification du code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from .cleaner import CleaningResult
from .config import Settings
from .sector_analysis import INSUFFICIENT

logger = logging.getLogger(__name__)

SEPARATOR = "=" * 78


@dataclass
class AnalysisBundle:
    """Ensemble des tables produites par le pipeline d'analyse."""

    cleaning: CleaningResult
    statistics: pd.DataFrame = field(default_factory=pd.DataFrame)
    account_overview: pd.DataFrame = field(default_factory=pd.DataFrame)
    objectives: pd.DataFrame = field(default_factory=pd.DataFrame)
    objective_evaluation: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_objectives: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_benchmark: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_comparison: pd.DataFrame = field(default_factory=pd.DataFrame)
    trends: pd.DataFrame = field(default_factory=pd.DataFrame)
    format_comparison: pd.DataFrame = field(default_factory=pd.DataFrame)
    best_formats: pd.DataFrame = field(default_factory=pd.DataFrame)
    sector_formats: pd.DataFrame = field(default_factory=pd.DataFrame)
    cm_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    method_comparison: pd.DataFrame = field(default_factory=pd.DataFrame)
    quality_report: pd.DataFrame = field(default_factory=pd.DataFrame)

    def tables(self) -> dict[str, pd.DataFrame]:
        """Tables exportables, dans l'ordre de lecture souhaite."""
        return {
            "statistiques_detaillees": self.statistics,
            "synthese_comptes": self.account_overview,
            "objectifs_performance": self.objectives,
            "evaluation_vs_objectifs": self.objective_evaluation,
            "objectifs_par_categorie": self.sector_objectives,
            "benchmark_sectoriel": self.sector_benchmark,
            "comparaison_secteur": self.sector_comparison,
            "tendances": self.trends,
            "comparaison_formats": self.format_comparison,
            "meilleurs_formats": self.best_formats,
            "formats_par_secteur": self.sector_formats,
            "synthese_community_managers": self.cm_summary,
            "comparaison_methodes_outliers": self.method_comparison,
            "qualite_donnees": self.quality_report,
        }


def build_quality_report(cleaning: CleaningResult) -> pd.DataFrame:
    """Consolide tous les indicateurs de tracabilite du nettoyage."""
    rows: list[dict[str, object]] = []

    def extend(section: str, entries: list[dict[str, object]]) -> None:
        rows.extend({"section": section, **entry} for entry in entries)

    extend(
        "Fichiers",
        [
            {"indicateur": "Fichiers analyses", "valeur": len(cleaning.mapping_reports)},
            {"indicateur": "Fichiers ignores (format inconnu)", "valeur": len(cleaning.skipped_files)},
            {"indicateur": "Comptes ecartes (volume insuffisant)", "valeur": len(cleaning.dropped_accounts)},
            {"indicateur": "  detail", "valeur": ", ".join(cleaning.dropped_accounts) or "-"},
        ],
    )
    for report in cleaning.mapping_reports:
        row = report.as_row()
        extend(
            "Mapping des colonnes",
            [
                {
                    "indicateur": f"{row['fichier']} ({row['plateforme']})",
                    "valeur": f"{row['colonnes_mappees']}/{row['colonnes_source']} colonnes utilisees",
                },
            ],
        )
        if row["champs_absents"]:
            extend(
                "Mapping des colonnes",
                [{"indicateur": f"  champs absents ({row['fichier']})", "valeur": row["champs_absents"]}],
            )

    if cleaning.sponsorship:
        extend("Publications sponsorisees", cleaning.sponsorship.summary_rows())
    if cleaning.dedup:
        extend("Deduplication", cleaning.dedup.summary_rows())
    if cleaning.validation:
        extend("Validation", cleaning.validation.summary_rows())

    extend(
        "Periode",
        [
            {"indicateur": "Debut", "valeur": str(cleaning.period_start)},
            {"indicateur": "Fin", "valeur": str(cleaning.period_end)},
            {"indicateur": "Publications hors periode", "valeur": cleaning.n_period_excluded},
        ],
    )
    return pd.DataFrame(rows)


def _format_line(text: str) -> str:
    return f"  - {text}"


def build_recommendations(bundle: AnalysisBundle, settings: Settings) -> str:
    """Genere le rapport de recommandations lisible par la direction."""
    cleaning = bundle.cleaning
    lines: list[str] = [
        SEPARATOR,
        "RAPPORT D'ANALYSE DES PERFORMANCES - COMMUNITY MANAGEMENT",
        SEPARATOR,
        f"Genere le : {datetime.now():%Y-%m-%d %H:%M}",
        f"Periode analysee : {cleaning.period_start:%Y-%m-%d} -> {cleaning.period_end:%Y-%m-%d}"
        if cleaning.period_start is not None and cleaning.period_end is not None
        else "Periode analysee : indeterminee",
        f"Publications organiques retenues : {len(cleaning.data)}",
        f"Comptes analyses : {cleaning.n_accounts}",
        "",
    ]

    # --- Qualite des donnees -------------------------------------------------
    lines += ["1. QUALITE ET PERIMETRE DES DONNEES", "-" * 78]
    if cleaning.sponsorship:
        lines.append(
            _format_line(
                f"{cleaning.sponsorship.n_sponsored} publication(s) sponsorisee(s) exclue(s) "
                f"({cleaning.sponsorship.pct_sponsored:.1f} % du total) : leur portee depend du "
                "budget publicitaire et non du travail editorial."
            )
        )
    if cleaning.dedup and cleaning.dedup.n_duplicate_ids:
        lines.append(
            _format_line(
                f"{cleaning.dedup.n_duplicate_ids} publication(s) presente(s) dans plusieurs "
                "fichiers ont ete fusionnees pour eviter un double comptage."
            )
        )
    if cleaning.validation and cleaning.validation.n_rejected:
        lines.append(
            _format_line(
                f"{cleaning.validation.n_rejected} ligne(s) mise(s) en quarantaine "
                "(voir la table qualite_donnees)."
            )
        )
    if cleaning.skipped_files:
        lines.append(
            _format_line(
                f"Fichier(s) ignore(s) car de format non reconnu : {', '.join(cleaning.skipped_files)}."
            )
        )
    lines.append("")

    # --- Formats -------------------------------------------------------------
    lines += ["2. PERFORMANCE PAR FORMAT", "-" * 78]
    if not bundle.format_comparison.empty:
        overall = (
            bundle.format_comparison.groupby("Format")
            .agg(comptes=("Compte", "nunique"), mediane=("Vues medianes", "median"))
            .sort_values("mediane", ascending=False)
        )
        for fmt, row in overall.iterrows():
            lines.append(
                _format_line(
                    f"{fmt.capitalize()} : {row['mediane']:.0f} vues medianes "
                    f"(sur {int(row['comptes'])} compte(s))."
                )
            )
        if len(overall) >= 2:
            best, worst = overall.index[0], overall.index[-1]
            if overall.loc[worst, "mediane"]:
                ratio = overall.loc[best, "mediane"] / overall.loc[worst, "mediane"]
                lines.append(
                    _format_line(
                        f"Globalement, le format {best} genere environ {ratio:.1f} fois plus de "
                        f"vues que le format {worst}."
                    )
                )
    if not bundle.best_formats.empty:
        for _, row in bundle.best_formats.iterrows():
            lines.append(
                _format_line(
                    f"{row['Compte']} : format le plus performant = {row['Meilleur format']} "
                    f"({row['Vues medianes']:.0f} vues medianes sur {int(row['Publications'])} publications)."
                )
            )
    lines.append("")

    # --- Tendances -----------------------------------------------------------
    lines += ["3. EVOLUTION DANS LE TEMPS", "-" * 78]
    if not bundle.trends.empty:
        trends = bundle.trends
        measurable = trends[trends["Variation (%)"].notna()]

        # Un recul generalise traduit un effet de plateforme, pas la somme
        # d'echecs individuels : le dire avant de lister les comptes evite que
        # la baisse brute soit lue comme une contre-performance de chaque CM.
        if not measurable.empty and "Variation plateforme (%)" in measurable.columns:
            declining = int((measurable["Variation (%)"] < 0).sum())
            if declining >= 0.7 * len(measurable) and len(measurable) >= 4:
                lines.append(
                    _format_line(
                        f"CONTEXTE : {declining} comptes sur {len(measurable)} reculent sur la "
                        "periode. Une baisse aussi generalisee traduit une evolution de la "
                        "plateforme (algorithme, saisonnalite), pas une contre-performance "
                        "individuelle. L'evaluation doit donc porter sur l'ecart au marche, "
                        "colonne 'Tendance relative', et non sur la variation brute."
                    )
                )
                lines.append("")

        for _, row in trends.iterrows():
            if row["Tendance"] == "Donnees insuffisantes":
                lines.append(
                    _format_line(f"{row['Compte']} : {row['Tendance'].lower()} ({row['Detail']}).")
                )
                continue
            relative = ""
            if "Tendance relative" in row and pd.notna(row.get("Ecart au marche (points)")):
                relative = (
                    f" | vs plateforme : {row['Ecart au marche (points)']:+.1f} pts "
                    f"-> {row['Tendance relative'].lower()}"
                )
            lines.append(
                _format_line(
                    f"{row['Compte']} : {row['Tendance'].lower()} "
                    f"({row['Mediane debut']:.0f} -> {row['Mediane fin']:.0f} vues medianes, "
                    f"soit {row['Variation (%)']:+.1f} %){relative}."
                )
            )
    lines.append("")

    # --- Objectifs -----------------------------------------------------------
    lines += ["4. OBJECTIFS DE PRIME", "-" * 78]
    if not bundle.objectives.empty:
        computed = bundle.objectives[bundle.objectives["Statut"] == "Objectifs calcules"]
        insufficient = bundle.objectives[bundle.objectives["Statut"] != "Objectifs calcules"]
        lines.append(
            _format_line(
                f"{len(computed)} combinaison(s) compte x plateforme x format disposent "
                f"d'objectifs chiffres exploitables."
            )
        )
        for _, row in computed.iterrows():
            lines.append(
                _format_line(
                    f"{row['Compte']} / {row['Plateforme']} / {row['Format']} : "
                    f"N1={row['Niveau 1']:.0f}, N2={row['Niveau 2']:.0f}, N3={row['Niveau 3']:.0f} vues "
                    f"(fiabilite {row['Fiabilite'].lower()}, {int(row['Publications'])} publications)."
                )
            )
        for _, row in insufficient.iterrows():
            lines.append(
                _format_line(
                    f"{row['Compte']} / {row['Plateforme']} / {row['Format']} : donnees insuffisantes "
                    f"({int(row['Publications'])} publications) - aucun objectif fiable ne peut etre fixe."
                )
            )

        dispersed = computed[
            computed["Avertissement"].str.contains("dispersee", na=False)
        ]
        if not dispersed.empty:
            lines.append("")
            lines.append(
                _format_line(
                    "ATTENTION - paliers 3 portes par des publications exceptionnelles, "
                    "a ne pas utiliser tels quels comme condition de prime :"
                )
            )
            for _, row in dispersed.iterrows():
                lines.append(
                    _format_line(
                        f"  {row['Compte']} / {row['Plateforme']} / {row['Format']} : "
                        f"N1={row['Niveau 1']:.0f} mais N3={row['Niveau 3']:.0f} "
                        f"({row['Dispersion N3/N1']:.1f} x). Quelques publications virales tirent "
                        "le palier haut ; le viser releve du pari, pas du travail regulier."
                    )
                )
    lines.append("")

    # --- Engagement ----------------------------------------------------------
    # --- Objectifs de categorie ---------------------------------------------
    lines += ["5. OBJECTIFS PAR CATEGORIE", "-" * 78]
    if bundle.sector_objectives.empty:
        lines.append(_format_line("Aucun objectif de categorie calculable."))
    else:
        computed = bundle.sector_objectives[
            bundle.sector_objectives["Statut"] == "Objectifs calcules"
        ]
        if computed.empty:
            lines.append(
                _format_line(
                    "Aucune categorie ne reunit assez de comptes et de publications pour "
                    "porter un objectif commun."
                )
            )
        else:
            lines.append(
                _format_line(
                    "Ces paliers decrivent le COMPTE TYPE de la categorie : chaque compte y "
                    "pese pour 1, quel que soit son volume de publication. Un calcul sur "
                    "l'ensemble des publications aurait donne le niveau du plus gros publieur, "
                    "pas celui de la categorie."
                )
            )
            lines.append("")
            for _, row in computed.iterrows():
                lines.append(
                    _format_line(
                        f"{row['Secteur']} / {row['Plateforme']} / {row['Format']} "
                        f"({int(row['Comptes qualifies'])} comptes) : "
                        f"N1={row['Niveau 1']:.0f} | N2={row['Niveau 2']:.0f} | "
                        f"N3={row['Niveau 3']:.0f} vues."
                    )
                )
                if str(row.get("Avertissement", "")).strip():
                    lines.append(_format_line(f"    {row['Avertissement']}"))

        insufficient = bundle.sector_objectives[
            bundle.sector_objectives["Statut"] != "Objectifs calcules"
        ]
        if not insufficient.empty:
            lines.append("")
            lines.append(
                _format_line(
                    f"{len(insufficient)} categorie(s) sans objectif commun, faute de comptes "
                    "ou de publications en nombre suffisant (detail dans "
                    "objectifs_par_categorie.csv)."
                )
            )
    lines.append("")

    lines += ["6. POINTS D'ATTENTION SUR L'ENGAGEMENT", "-" * 78]
    if not bundle.statistics.empty and "engagement_median" in bundle.statistics.columns:
        eligible = bundle.statistics[bundle.statistics["sufficient"]]
        if not eligible.empty:
            reference = eligible["engagement_median"].median()
            weak = eligible[eligible["engagement_median"] < 0.7 * reference]
            for _, row in weak.iterrows():
                lines.append(
                    _format_line(
                        f"{row['account_name']} / {row['format']} : engagement median "
                        f"{row['engagement_median']:.2f} % contre {reference:.2f} % en reference, "
                        "malgre une portee comparable - le contenu touche mais fait peu reagir."
                    )
                )
            if weak.empty:
                lines.append(_format_line("Aucun ecart d'engagement significatif detecte."))
    lines.append("")

    # --- Benchmark sectoriel -------------------------------------------------
    lines += ["7. BENCHMARK SECTORIEL", "-" * 78]
    if not bundle.sector_benchmark.empty:
        reliable = bundle.sector_benchmark[bundle.sector_benchmark["Fiabilite"] != INSUFFICIENT]
        unreliable = bundle.sector_benchmark[bundle.sector_benchmark["Fiabilite"] == INSUFFICIENT]
        for _, row in reliable.iterrows():
            lines.append(
                _format_line(
                    f"Secteur {row['Secteur']} / {row['Plateforme']} / {row['Format']} : "
                    f"mediane sectorielle {row['Mediane sectorielle (comptes)']:.0f} vues "
                    f"({int(row['Comptes qualifies'])} comptes, {int(row['Publications'])} publications)."
                )
            )
        if not unreliable.empty:
            lines.append(
                _format_line(
                    f"{len(unreliable)} combinaison(s) sectorielle(s) marquee(s) INSUFFISANT : "
                    "elles ne doivent PAS servir de reference pour une prime."
                )
            )
        if reliable.empty:
            # Cas frequent en debut de projet : le diagnostic doit etre actionnable,
            # sinon la direction ne sait pas ce qui manque pour debloquer la situation.
            accounts_per_sector = (
                cleaning.data.groupby("sector")["account_name"].nunique().sort_values(ascending=False)
            )
            lines.append(
                _format_line(
                    "AUCUN benchmark sectoriel n'est exploitable en l'etat. Repartition actuelle : "
                    + ", ".join(f"{sector} = {n} compte(s)" for sector, n in accounts_per_sector.items())
                    + f". Le seuil configure exige {settings.sector.min_accounts} comptes par secteur."
                )
            )
            lines.append(
                _format_line(
                    "Deux leviers : rattacher davantage de comptes a un meme secteur dans "
                    "config/clients.csv (regroupements plus larges, ex. 'Commerce de detail'), "
                    "ou abaisser sector.min_accounts dans settings.yaml en acceptant une "
                    "reference moins solide. Tant que ce n'est pas fait, l'evaluation doit "
                    "reposer sur l'historique propre a chaque compte (section 4)."
                )
            )
    lines.append("")

    # --- Limites -------------------------------------------------------------
    lines += [
        "7. LIMITES METHODOLOGIQUES A GARDER EN TETE",
        "-" * 78,
        _format_line(
            "Les seuils refletent l'historique du compte : ils doivent etre recalcules "
            "periodiquement (trimestriellement) car l'algorithme Meta et la saisonnalite evoluent."
        ),
        _format_line(
            "Les vues ne sont pas normalisees par la taille de l'audience : les exports par "
            "publication ne contiennent pas le nombre d'abonnes."
        ),
        _format_line(
            "Les publications exceptionnelles sont signalees mais jamais supprimees : une "
            "publication virale est une performance reelle, pas une erreur de mesure."
        ),
        _format_line(
            "Un benchmark sectoriel marque INSUFFISANT est indicatif seulement et ne doit pas "
            "fonder une decision de prime."
        ),
        SEPARATOR,
    ]
    return "\n".join(lines)


def export_all(bundle: AnalysisBundle, settings: Settings) -> dict[str, Path]:
    """Ecrit toutes les sorties sur disque et retourne les chemins produits."""
    paths: dict[str, Path] = {}
    csv_dir = settings.output_dir / "csv"
    reports_dir = settings.output_dir / "reports"
    excel_dir = settings.output_dir / "excel"
    for directory in (csv_dir, reports_dir, excel_dir):
        directory.mkdir(parents=True, exist_ok=True)

    for name, table in bundle.tables().items():
        if table.empty:
            continue
        path = csv_dir / f"{name}.csv"
        table.to_csv(path, index=False, encoding="utf-8-sig")
        paths[name] = path

    if settings.output.excel:
        excel_path = excel_dir / "analyse_performances.xlsx"
        try:
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                for name, table in bundle.tables().items():
                    if table.empty:
                        continue
                    table.to_excel(writer, sheet_name=name[:31], index=False)
            paths["excel"] = excel_path
        except (OSError, ValueError) as exc:
            logger.error("Export Excel impossible : %s", exc)

    report_path = reports_dir / "rapport_recommandations.txt"
    report_path.write_text(build_recommendations(bundle, settings), encoding="utf-8")
    paths["rapport"] = report_path

    logger.info("%d sortie(s) ecrite(s) dans %s", len(paths), settings.output_dir)
    return paths

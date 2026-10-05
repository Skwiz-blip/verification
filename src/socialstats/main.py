"""Point d'entree en ligne de commande.

Usage :
    python -m socialstats.main
    python -m socialstats.main --accounts "Abi's Cream" "Bonici Africa"
    python -m socialstats.main --period last_3_months --no-charts
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from . import __version__
from .cleaner import run_cleaning, save_cleaned
from .cm_rollup import rollup_by_cm
from .config import ConfigError, Settings, load_settings
from .descriptive import account_overview, group_statistics
from .format_analysis import best_format_per_group, compare_formats, sector_format_summary
from .loader import LoaderError
from .objectives import (
    build_objectives,
    build_sector_objectives,
    evaluate_against_objectives,
)
from .outliers import compare_methods, flag_outliers
from .reporting import AnalysisBundle, build_quality_report, export_all
from .schema_mapper import SchemaError
from .sector_analysis import build_sector_benchmark, compare_accounts_to_sector
from .trends import add_relative_trend, analyze_trends, monthly_evolution
from .visualization import generate_all

logger = logging.getLogger("socialstats")


def configure_logging(verbose: bool, log_file: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="socialstats",
        description="Analyse des performances Community Management a partir d'exports Meta.",
    )
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--config", type=Path, default=default_root / "config",
                        help="Dossier de configuration (defaut : ./config)")
    parser.add_argument("--data", type=Path, default=None,
                        help="Dossier des CSV bruts (defaut : ./data/raw)")
    parser.add_argument("--accounts", nargs="+", default=None,
                        help="Restreint l'analyse a certains comptes (defaut : tous)")
    parser.add_argument("--sectors", nargs="+", default=None,
                        help="Restreint l'analyse a certains secteurs")
    parser.add_argument("--period", default=None,
                        choices=["all", "last_3_months", "last_6_months", "last_12_months"],
                        help="Surcharge la periode definie dans settings.yaml")
    parser.add_argument("--min-observations", type=int, default=None,
                        help="Surcharge le nombre minimum de publications par groupe")
    parser.add_argument("--no-charts", action="store_true", help="Desactive les graphiques")
    parser.add_argument("--no-excel", action="store_true", help="Desactive l'export Excel")
    parser.add_argument("--verbose", action="store_true", help="Journalisation detaillee")
    parser.add_argument("--version", action="version", version=f"socialstats {__version__}")
    return parser.parse_args(argv)


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Applique les surcharges CLI par-dessus la configuration fichier."""
    if args.period:
        settings.analysis_period = args.period
    if args.min_observations is not None:
        settings.min_observations = args.min_observations
    if args.no_charts:
        settings.output = type(settings.output)(
            charts=False, excel=settings.output.excel, chart_dpi=settings.output.chart_dpi
        )
    if args.no_excel:
        settings.output = type(settings.output)(
            charts=settings.output.charts, excel=False, chart_dpi=settings.output.chart_dpi
        )
    return settings


def filter_scope(frame: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    """Restreint le perimetre d'analyse aux comptes / secteurs demandes."""
    out = frame
    if args.accounts:
        requested = set(args.accounts)
        available = set(out["account_name"].unique())
        unknown = requested - available
        if unknown:
            logger.warning("Compte(s) inconnu(s) ignore(s) : %s", ", ".join(sorted(unknown)))
        out = out[out["account_name"].isin(requested)]
    if args.sectors:
        out = out[out["sector"].isin(set(args.sectors))]
    if out.empty:
        raise SystemExit("Aucune publication ne correspond au perimetre demande.")
    return out


def run_analysis(settings: Settings, args: argparse.Namespace) -> AnalysisBundle:
    """Execute le pipeline complet et assemble toutes les tables."""
    cleaning = run_cleaning(settings)
    save_cleaned(cleaning, settings)

    data = filter_scope(cleaning.data, args)
    cleaning.data = data

    data = flag_outliers(
        data,
        value_column="views_organic_resolved",
        group_columns=["account_name", "platform", "format"],
        method=settings.outliers.method,
        multiplier=settings.outliers.multiplier,
        mad_threshold=settings.outliers.mad_threshold,
    )

    statistics = group_statistics(data, settings)
    objectives = build_objectives(data, settings)
    benchmark = build_sector_benchmark(data, settings)
    sector_objectives = build_sector_objectives(data, settings)
    format_comparison = compare_formats(data, settings)

    bundle = AnalysisBundle(
        cleaning=cleaning,
        statistics=statistics,
        account_overview=account_overview(data, settings),
        objectives=objectives,
        objective_evaluation=evaluate_against_objectives(data, objectives),
        sector_objectives=sector_objectives,
        sector_benchmark=benchmark,
        sector_comparison=compare_accounts_to_sector(data, benchmark, settings),
        trends=add_relative_trend(analyze_trends(data, settings), settings),
        format_comparison=format_comparison,
        best_formats=best_format_per_group(format_comparison),
        sector_formats=sector_format_summary(data, settings),
        cm_summary=rollup_by_cm(data, settings),
        method_comparison=compare_methods(
            data["views_organic_resolved"],
            settings.outliers.multiplier,
            settings.outliers.mad_threshold,
        ),
        quality_report=build_quality_report(cleaning),
    )

    paths = export_all(bundle, settings)
    generate_all(
        data,
        format_comparison,
        monthly_evolution(data),
        bundle.sector_comparison,
        objectives,
        settings,
    )

    print("\nAnalyse terminee.")
    print(f"  Publications organiques analysees : {len(data)}")
    print(f"  Comptes                           : {data['account_name'].nunique()}")
    if "rapport" in paths:
        print(f"  Rapport                           : {paths['rapport']}")
    print(f"  Sorties                           : {settings.output_dir}")
    return bundle


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    try:
        settings = load_settings(args.config)
        if args.data is not None:
            settings.project_root = args.data.resolve().parents[1]
        settings = apply_overrides(settings, args)
        configure_logging(args.verbose, settings.output_dir / "reports" / "execution.log")
        run_analysis(settings, args)
    except (ConfigError, LoaderError, SchemaError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrompu par l'utilisateur.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

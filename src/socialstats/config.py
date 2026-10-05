"""Chargement et validation de la configuration du projet."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

VALID_PERIODS = {"all", "last_3_months", "last_6_months", "last_12_months", "custom"}
VALID_OUTLIER_METHODS = {"iqr", "mad"}
UNCATEGORIZED = "Non categorise"


class ConfigError(Exception):
    """Configuration invalide ou introuvable."""


@dataclass(frozen=True)
class ReliabilityThresholds:
    insufficient_below: int = 5
    low_below: int = 10
    medium_below: int = 30

    def label(self, n: int) -> str:
        """Traduit un effectif en niveau de fiabilite lisible."""
        if n < self.insufficient_below:
            return "Insuffisante"
        if n < self.low_below:
            return "Faible"
        if n < self.medium_below:
            return "Moyenne"
        return "Elevee"


@dataclass(frozen=True)
class SectorThresholds:
    min_accounts: int = 3
    min_publications: int = 20
    min_observations_per_account: int = 5
    # Categories analysees malgre un nombre de comptes sous le seuil.
    # Derogation explicite et nominative : le resultat reste marque comme
    # repose sur peu de comptes, mais il est calcule et publie.
    forced_sectors: tuple[str, ...] = ()

    def is_forced(self, sector: str) -> bool:
        return str(sector).strip().lower() in {s.lower() for s in self.forced_sectors}

    def required_accounts(self, sector: str) -> int:
        """Nombre de comptes exige pour cette categorie (2 si derogation)."""
        return 2 if self.is_forced(sector) else self.min_accounts


@dataclass(frozen=True)
class ObjectiveSettings:
    level_1_quantile: float = 0.50
    level_2_quantile: float = 0.70
    level_3_quantile: float = 0.80
    rounding: int = 5
    # Au-dela de ce rapport N3/N1, le palier haut est juge trop dependant de
    # publications exceptionnelles pour constituer un objectif de travail.
    max_level_spread: float = 5.0

    @property
    def quantiles(self) -> tuple[float, float, float]:
        return (self.level_1_quantile, self.level_2_quantile, self.level_3_quantile)


@dataclass(frozen=True)
class OutlierSettings:
    method: str = "iqr"
    multiplier: float = 1.5
    mad_threshold: float = 3.5


@dataclass(frozen=True)
class TrendSettings:
    min_observations: int = 8
    change_threshold_pct: float = 15.0


@dataclass(frozen=True)
class OutputSettings:
    charts: bool = True
    excel: bool = True
    chart_dpi: int = 150


@dataclass
class Settings:
    """Configuration complete, deja validee."""

    project_root: Path
    analysis_period: str = "all"
    custom_start: pd.Timestamp | None = None
    custom_end: pd.Timestamp | None = None
    period_reference: str = "data_max"
    exclude_sponsored: bool = True
    min_observations: int = 10
    # Volume minimum de publications pour qu'un compte entre dans l'analyse.
    min_publications_per_account: int = 10
    reliability: ReliabilityThresholds = field(default_factory=ReliabilityThresholds)
    sector: SectorThresholds = field(default_factory=SectorThresholds)
    objectives: ObjectiveSettings = field(default_factory=ObjectiveSettings)
    outliers: OutlierSettings = field(default_factory=OutlierSettings)
    trend: TrendSettings = field(default_factory=TrendSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    format_aliases: dict[str, str] = field(default_factory=dict)
    detect_reel_from_permalink: bool = True
    excluded_formats: tuple[str, ...] = ()
    column_mappings: dict[str, Any] = field(default_factory=dict)
    clients: pd.DataFrame = field(default_factory=pd.DataFrame)

    # --- Chemins ------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.project_root / "data" / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.project_root / "data" / "processed"

    @property
    def output_dir(self) -> Path:
        return self.project_root / "output"

    def _lookup(self, account_name: str, column: str) -> str | None:
        """Retrouve une propriete declaree d'un compte.

        Le rapprochement se fait sur le nom NORMALISE : les exports Instagram
        ecrivent frequemment les noms en caracteres stylises, et un meme compte
        peut apparaitre avec une casse differente selon la plateforme.
        """
        if self.clients.empty or column not in self.clients.columns:
            return None
        from .schema_mapper import normalize_account_name

        key = normalize_account_name(account_name)
        match = self.clients.loc[self.clients["_key"] == key, column]
        if match.empty or pd.isna(match.iloc[0]) or not str(match.iloc[0]).strip():
            return None
        return str(match.iloc[0]).strip()

    def sector_of(self, account_name: str) -> str:
        """Secteur declare d'un compte, ou 'Non categorise' si absent."""
        return self._lookup(account_name, "sector") or UNCATEGORIZED

    def client_of(self, account_name: str) -> str:
        """Client (marque) auquel appartient le compte.

        Un meme client peut posseder plusieurs comptes : une page Facebook et
        un profil Instagram, voire plusieurs pays. A defaut de declaration, le
        nom du compte fait office de client.
        """
        return self._lookup(account_name, "client") or account_name

    def cm_of(self, account_name: str) -> str | None:
        """Community Manager declare pour un compte (None si non renseigne)."""
        return self._lookup(account_name, "cm_name")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un mapping YAML.")
    return data


def _load_clients(path: Path) -> pd.DataFrame:
    """Charge clients.csv. Un fichier absent n'est pas bloquant."""
    if not path.exists():
        logger.warning(
            "clients.csv absent (%s) : tous les comptes seront '%s'.", path, UNCATEGORIZED
        )
        return pd.DataFrame(columns=["account_name", "sector", "cm_name"])

    from .schema_mapper import normalize_account_name

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    if "account_name" not in df.columns:
        raise ConfigError(f"{path} doit contenir une colonne 'account_name'.")
    for optional, default in (("sector", UNCATEGORIZED), ("client", pd.NA), ("cm_name", pd.NA)):
        if optional not in df.columns:
            df[optional] = default

    df["account_name"] = df["account_name"].str.strip()
    df["_key"] = df["account_name"].map(normalize_account_name)

    duplicated = set(df["_key"][df["_key"].duplicated() & df["_key"].ne("")])
    if duplicated:
        details = [
            f"{key!r} <- {sorted(df.loc[df['_key'] == key, 'account_name'])}"
            for key in sorted(duplicated)
        ]
        raise ConfigError(
            f"Comptes en double dans {path} apres normalisation des noms. "
            "Ces libelles designent le meme compte, une seule ligne suffit : "
            + " ; ".join(details)
        )
    return df


def _parse_date(value: Any, label: str) -> pd.Timestamp | None:
    if value in (None, "", "null"):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ConfigError(f"Date invalide pour {label} : {value!r}")
    return parsed


def load_settings(config_dir: Path | str, project_root: Path | str | None = None) -> Settings:
    """Charge settings.yaml, column_mappings.yaml et clients.csv, puis valide le tout."""
    config_dir = Path(config_dir)
    root = Path(project_root) if project_root else config_dir.parent

    raw = _read_yaml(config_dir / "settings.yaml")
    mappings = _read_yaml(config_dir / "column_mappings.yaml")
    clients = _load_clients(config_dir / "clients.csv")

    period = str(raw.get("analysis_period", "all"))
    if period not in VALID_PERIODS:
        raise ConfigError(
            f"analysis_period={period!r} invalide. Valeurs possibles : {sorted(VALID_PERIODS)}"
        )

    custom = raw.get("custom_period") or {}
    start = _parse_date(custom.get("start"), "custom_period.start")
    end = _parse_date(custom.get("end"), "custom_period.end")
    if period == "custom" and start is None and end is None:
        raise ConfigError("analysis_period='custom' exige custom_period.start et/ou .end.")
    if start is not None and end is not None and start > end:
        raise ConfigError("custom_period.start doit preceder custom_period.end.")

    rel_raw = raw.get("reliability") or {}
    reliability = ReliabilityThresholds(
        insufficient_below=int(rel_raw.get("insufficient_below", 5)),
        low_below=int(rel_raw.get("low_below", 10)),
        medium_below=int(rel_raw.get("medium_below", 30)),
    )
    if not (
        reliability.insufficient_below
        <= reliability.low_below
        <= reliability.medium_below
    ):
        raise ConfigError(
            "reliability : les seuils doivent etre croissants "
            "(insufficient_below <= low_below <= medium_below)."
        )

    obj_raw = raw.get("objectives") or {}
    objectives = ObjectiveSettings(
        level_1_quantile=float(obj_raw.get("level_1_quantile", 0.50)),
        level_2_quantile=float(obj_raw.get("level_2_quantile", 0.70)),
        level_3_quantile=float(obj_raw.get("level_3_quantile", 0.80)),
        rounding=int(obj_raw.get("rounding", 5)),
        max_level_spread=float(obj_raw.get("max_level_spread", 5.0)),
    )
    q1, q2, q3 = objectives.quantiles
    if not all(0.0 < q < 1.0 for q in (q1, q2, q3)):
        raise ConfigError("Les quantiles d'objectifs doivent etre strictement entre 0 et 1.")
    if not q1 < q2 < q3:
        raise ConfigError(
            f"Les quantiles doivent etre strictement croissants (recu : {q1}, {q2}, {q3})."
        )
    if objectives.rounding < 1:
        raise ConfigError("objectives.rounding doit valoir au moins 1.")

    out_raw = raw.get("outliers") or {}
    outliers = OutlierSettings(
        method=str(out_raw.get("method", "iqr")).lower(),
        multiplier=float(out_raw.get("multiplier", 1.5)),
        mad_threshold=float(out_raw.get("mad_threshold", 3.5)),
    )
    if outliers.method not in VALID_OUTLIER_METHODS:
        raise ConfigError(
            f"outliers.method={outliers.method!r} invalide. "
            f"Valeurs possibles : {sorted(VALID_OUTLIER_METHODS)}"
        )
    if outliers.multiplier <= 0:
        raise ConfigError("outliers.multiplier doit etre strictement positif.")

    sector_raw = raw.get("sector") or {}
    trend_raw = raw.get("trend") or {}
    output_raw = raw.get("output") or {}

    aliases = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in (raw.get("format_aliases") or {}).items()
    }

    settings = Settings(
        project_root=root,
        analysis_period=period,
        custom_start=start,
        custom_end=end,
        period_reference=str(raw.get("period_reference", "data_max")),
        exclude_sponsored=bool(raw.get("exclude_sponsored", True)),
        min_observations=int(raw.get("min_observations", 10)),
        min_publications_per_account=int(raw.get("min_publications_per_account", 10)),
        reliability=reliability,
        sector=SectorThresholds(
            min_accounts=int(sector_raw.get("min_accounts", 3)),
            min_publications=int(sector_raw.get("min_publications", 20)),
            min_observations_per_account=int(sector_raw.get("min_observations_per_account", 5)),
            forced_sectors=tuple(
                str(x).strip() for x in (sector_raw.get("forced_sectors") or [])
            ),
        ),
        objectives=objectives,
        outliers=outliers,
        trend=TrendSettings(
            min_observations=int(trend_raw.get("min_observations", 8)),
            change_threshold_pct=float(trend_raw.get("change_threshold_pct", 15.0)),
        ),
        output=OutputSettings(
            charts=bool(output_raw.get("charts", True)),
            excel=bool(output_raw.get("excel", True)),
            chart_dpi=int(output_raw.get("chart_dpi", 150)),
        ),
        format_aliases=aliases,
        detect_reel_from_permalink=bool(raw.get("detect_reel_from_permalink", True)),
        excluded_formats=tuple(
            str(f).strip().lower() for f in (raw.get("excluded_formats") or [])
        ),
        column_mappings=mappings,
        clients=clients,
    )

    if settings.period_reference not in {"data_max", "today"}:
        raise ConfigError("period_reference doit valoir 'data_max' ou 'today'.")

    logger.info(
        "Configuration chargee : periode=%s, sponsorise_exclu=%s, min_obs=%d, %d client(s) declare(s)",
        settings.analysis_period,
        settings.exclude_sponsored,
        settings.min_observations,
        len(clients),
    )
    return settings

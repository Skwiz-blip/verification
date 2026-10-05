"""Fixtures partagees : configuration minimale et jeux de donnees synthetiques."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from socialstats.config import (  # noqa: E402
    ObjectiveSettings,
    OutlierSettings,
    OutputSettings,
    ReliabilityThresholds,
    SectorThresholds,
    Settings,
    TrendSettings,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Configuration de test, independante des fichiers du projet."""
    return Settings(
        project_root=tmp_path,
        analysis_period="all",
        exclude_sponsored=True,
        min_observations=10,
        reliability=ReliabilityThresholds(5, 10, 30),
        sector=SectorThresholds(min_accounts=3, min_publications=20, min_observations_per_account=5),
        objectives=ObjectiveSettings(0.50, 0.70, 0.80, rounding=5),
        outliers=OutlierSettings(method="iqr", multiplier=1.5, mad_threshold=3.5),
        trend=TrendSettings(min_observations=8, change_threshold_pct=15.0),
        output=OutputSettings(charts=False, excel=False, chart_dpi=100),
        format_aliases={"photos": "photo", "videos": "video", "reels": "reel", "texte": "text"},
        detect_reel_from_permalink=True,
        excluded_formats=("text", "link"),
        clients=pd.DataFrame(
            {
                "account_name": ["Compte A", "Compte B", "Compte C"],
                "sector": ["Optique", "Optique", "Optique"],
                "cm_name": ["", "", ""],
            }
        ),
    )


from helpers import make_posts  # noqa: E402


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """Trois comptes du meme secteur, de volumes tres differents.

    Le desequilibre est volontaire : il sert a verifier que le benchmark
    sectoriel n'est pas dicte par le compte le plus prolifique.
    """
    return pd.concat(
        [
            make_posts("Compte A", "photo", [100.0 + i for i in range(60)]),
            make_posts("Compte B", "photo", [200.0 + i for i in range(12)]),
            make_posts("Compte C", "photo", [300.0 + i for i in range(8)]),
        ],
        ignore_index=True,
    )

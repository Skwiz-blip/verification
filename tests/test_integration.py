"""Test d'integration sur les exports Meta reels du dossier data/raw.

Ces tests sont automatiquement ignores si les fichiers ne sont pas presents,
afin que la suite reste executable sur un poste vierge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from socialstats.cleaner import run_cleaning
from socialstats.config import load_settings
from socialstats.objectives import build_objectives
from socialstats.sector_analysis import build_sector_benchmark

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

pytestmark = pytest.mark.skipif(
    not RAW_DIR.exists() or not list(RAW_DIR.glob("*.csv")),
    reason="Aucun export reel dans data/raw",
)


@pytest.fixture(scope="module")
def real_settings():
    return load_settings(CONFIG_DIR)


@pytest.fixture(scope="module")
def real_result(real_settings):
    return run_cleaning(real_settings)


class TestRealExports:
    def test_tous_les_fichiers_sont_reconnus(self, real_result):
        assert real_result.skipped_files == []
        assert len(real_result.mapping_reports) == len(list(RAW_DIR.glob("*.csv")))

    def test_toutes_les_dates_sont_interpretees(self, real_result):
        # La colonne "Date" vaut "Global" : c'est "Heure de publication" qui compte.
        assert real_result.data["published_at"].notna().all()

    def test_les_doublons_inter_fichiers_sont_fusionnes(self, real_result):
        assert real_result.dedup.n_duplicate_ids > 0
        assert not real_result.data["post_id"].duplicated().any()

    def test_le_sponsorise_est_detecte_malgre_le_statut_vide(self, real_result):
        """La colonne officielle est vide : seuls les signaux boostes fonctionnent."""
        assert real_result.sponsorship.n_sponsored > 0
        assert not real_result.data["is_sponsored"].any()

    def test_les_reels_sont_distingues_des_videos(self, real_result):
        formats = set(real_result.data["format"].unique())
        assert "reel" in formats

    def test_chaque_publication_a_des_vues_organiques(self, real_result):
        assert real_result.data["views_organic_resolved"].notna().all()

    def test_les_objectifs_respectent_l_intention_metier(self, real_settings, real_result):
        objectives = build_objectives(real_result.data, real_settings)
        computed = objectives[objectives["Statut"] == "Objectifs calcules"]
        assert not computed.empty
        assert (computed["Niveau 1"] < computed["Niveau 2"]).all()
        assert (computed["Niveau 2"] < computed["Niveau 3"]).all()
        # Tolerance large : les distributions reelles sont discretes.
        assert 40 <= computed["Atteinte N1 (%)"].mean() <= 58
        assert 28 <= computed["Atteinte N2 (%)"].mean() <= 38
        assert 18 <= computed["Atteinte N3 (%)"].mean() <= 28

    def test_les_petits_echantillons_n_ont_pas_de_seuil(self, real_settings, real_result):
        objectives = build_objectives(real_result.data, real_settings)
        small = objectives[objectives["Publications"] < real_settings.min_observations]
        assert small["Niveau 1"].isna().all()

    def test_le_benchmark_signale_sa_fiabilite(self, real_settings, real_result):
        benchmark = build_sector_benchmark(real_result.data, real_settings)
        assert not benchmark.empty
        assert benchmark["Fiabilite"].notna().all()

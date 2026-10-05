"""Tests du benchmark sectoriel."""

from __future__ import annotations

import pandas as pd
from helpers import make_posts

from socialstats.sector_analysis import (
    INSUFFICIENT,
    RELIABLE,
    build_sector_benchmark,
    compare_accounts_to_sector,
)


class TestBuildSectorBenchmark:
    def test_gros_compte_n_ecrase_pas_les_petits(self, settings):
        """Point central de la methode : chaque compte pese pour 1.

        Un compte de 60 publications a ~100 vues et deux comptes plus petits a
        200 et 300 vues. La moyenne poolee serait tiree vers 100 par le volume,
        alors que le compte typique du secteur tourne autour de 200.
        """
        data = pd.concat(
            [
                make_posts("Compte A", "photo", [100.0] * 60),
                make_posts("Compte B", "photo", [200.0] * 12),
                make_posts("Compte C", "photo", [300.0] * 8),
            ],
            ignore_index=True,
        )
        row = build_sector_benchmark(data, settings).iloc[0]
        assert row["Mediane sectorielle (comptes)"] == 200.0
        assert row["Mediane poolee"] == 100.0

    def test_benchmark_exploitable_si_seuils_atteints(self, settings, sample_data):
        row = build_sector_benchmark(sample_data, settings).iloc[0]
        assert row["Comptes qualifies"] == 3
        assert row["Fiabilite"] == RELIABLE

    def test_secteur_a_un_seul_compte_est_toujours_insuffisant(self, settings):
        data = make_posts("Compte Unique", "photo", [100.0] * 50)
        row = build_sector_benchmark(data, settings).iloc[0]
        assert row["Fiabilite"] == INSUFFICIENT
        assert "compte(s) qualifie(s)" in row["Motif"]

    def test_publications_insuffisantes_marquees(self, settings):
        data = pd.concat(
            [
                make_posts("A", "photo", [100.0] * 5),
                make_posts("B", "photo", [100.0] * 5),
                make_posts("C", "photo", [100.0] * 5),
            ],
            ignore_index=True,
        )
        row = build_sector_benchmark(data, settings).iloc[0]
        assert row["Fiabilite"] == INSUFFICIENT
        assert "publication(s)" in row["Motif"]

    def test_compte_trop_petit_n_est_pas_qualifie(self, settings):
        """Un compte a 2 publications ne doit pas peser autant qu'un compte etabli."""
        data = pd.concat(
            [
                make_posts("A", "photo", [100.0] * 30),
                make_posts("B", "photo", [100.0] * 30),
                make_posts("C", "photo", [9999.0, 9999.0]),
            ],
            ignore_index=True,
        )
        row = build_sector_benchmark(data, settings).iloc[0]
        assert row["Nombre comptes"] == 3
        assert row["Comptes qualifies"] == 2
        assert row["Mediane sectorielle (comptes)"] == 100.0


class TestCompareAccountsToSector:
    def test_positionne_les_comptes(self, settings, sample_data):
        benchmark = build_sector_benchmark(sample_data, settings)
        result = compare_accounts_to_sector(sample_data, benchmark, settings)
        assert set(result["Compte"]) == {"Compte A", "Compte B", "Compte C"}
        assert (result["Positionnement"] != "").all()

    def test_benchmark_non_fiable_donne_un_positionnement_indicatif(self, settings):
        data = make_posts("Compte Unique", "photo", [100.0] * 50)
        benchmark = build_sector_benchmark(data, settings)
        result = compare_accounts_to_sector(data, benchmark, settings)
        assert result["Positionnement"].iloc[0] == "Indicatif seulement"

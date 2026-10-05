"""Tests de la construction des objectifs de prime."""

from __future__ import annotations

import pandas as pd
import pytest

from socialstats.objectives import (
    attainment_rate,
    build_objectives,
    build_sector_objectives,
    round_threshold,
)

from helpers import make_posts


class TestRoundThreshold:
    def test_arrondit_vers_le_haut(self):
        # Ne jamais annoncer un seuil plus facile que celui calcule.
        assert round_threshold(78.4, 5) == 80.0
        assert round_threshold(76.0, 5) == 80.0

    def test_arrondi_unitaire(self):
        assert round_threshold(78.4, 1) == 79.0

    def test_valeur_manquante(self):
        assert pd.isna(round_threshold(float("nan"), 5))


class TestBuildObjectives:
    def test_niveaux_strictement_croissants(self, settings):
        data = make_posts("Compte A", "photo", [float(i) for i in range(10, 60)])
        result = build_objectives(data, settings)
        row = result.iloc[0]
        assert row["Niveau 1"] < row["Niveau 2"] < row["Niveau 3"]

    def test_taux_d_atteinte_conformes_a_l_intention_metier(self, settings):
        """Verifie la promesse : ~50 %, ~30-35 %, ~20 % des publications."""
        data = make_posts("Compte A", "photo", [float(i) for i in range(1, 101)])
        row = build_objectives(data, settings).iloc[0]
        assert 40 <= row["Atteinte N1 (%)"] <= 55
        assert 25 <= row["Atteinte N2 (%)"] <= 38
        assert 15 <= row["Atteinte N3 (%)"] <= 27

    def test_echantillon_insuffisant_ne_produit_aucun_seuil(self, settings):
        """Sur 8 publications, un P80 reposerait sur 1 ou 2 points."""
        data = make_posts("Petit Compte", "photo", [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0])
        row = build_objectives(data, settings).iloc[0]
        assert row["Niveau 1"] is None
        assert "insuffisant" in row["Statut"].lower()
        assert row["Avertissement"]

    def test_publication_virale_ne_fait_pas_exploser_les_seuils(self, settings):
        """Argument central du choix des quantiles contre la moyenne."""
        normal = [100.0] * 30
        sans_viral = build_objectives(make_posts("A", "photo", normal), settings).iloc[0]
        avec_viral = build_objectives(
            make_posts("A", "photo", normal + [500000.0]), settings
        ).iloc[0]
        assert avec_viral["Niveau 1"] == sans_viral["Niveau 1"]
        assert avec_viral["Niveau 3"] <= sans_viral["Niveau 3"] * 1.1

    def test_distribution_loterie_est_signalee(self, settings):
        """Quelques publications virales ne doivent pas devenir une cible de prime.

        Cas reel rencontre : un compte a 105 vues medianes se voyait attribuer
        un palier 3 a 1815 vues, atteignable uniquement par viralite.
        """
        values = [100.0] * 17 + [5000.0] * 6
        row = build_objectives(make_posts("A", "reel", values), settings).iloc[0]
        assert row["Dispersion N3/N1"] > 5
        assert "dispersee" in row["Avertissement"]

    def test_distribution_reguliere_n_est_pas_signalee(self, settings):
        row = build_objectives(
            make_posts("A", "photo", [float(i) for i in range(100, 160)]), settings
        ).iloc[0]
        assert "dispersee" not in row["Avertissement"]

    def test_formats_exclus_sont_ignores(self, settings):
        data = pd.concat(
            [
                make_posts("A", "photo", [float(i) for i in range(20)]),
                make_posts("A", "text", [float(i) for i in range(20)]),
            ],
            ignore_index=True,
        )
        result = build_objectives(data, settings)
        assert "text" not in result["Format"].tolist()

    def test_fiabilite_reflete_l_effectif(self, settings):
        petit = build_objectives(make_posts("A", "photo", [1.0] * 12), settings).iloc[0]
        grand = build_objectives(make_posts("B", "photo", [1.0] * 50), settings).iloc[0]
        assert petit["Fiabilite"] == "Moyenne"
        assert grand["Fiabilite"] == "Elevee"


class TestAttainmentRate:
    def test_calcul_simple(self):
        assert attainment_rate(pd.Series([10.0, 20.0, 30.0, 40.0]), 30.0) == 50.0

    def test_serie_vide(self):
        assert pd.isna(attainment_rate(pd.Series([], dtype=float), 10.0))


class TestBuildSectorObjectives:
    def _frame(self, rows: list[tuple[str, str, float]]) -> pd.DataFrame:
        """rows = (secteur, compte, valeur), plateforme/format constants."""
        return pd.DataFrame(
            {
                "sector": [r[0] for r in rows],
                "account_name": [r[1] for r in rows],
                "platform": "instagram",
                "format": "photo",
                "views_organic_resolved": [r[2] for r in rows],
                "engagement_rate": 5.0,
            }
        )

    def test_un_gros_compte_n_ecrase_pas_la_categorie(self, settings):
        """Le coeur de la methode : chaque compte pese 1, pas son volume.

        Un compte qui publie beaucoup et performe fort ne doit pas tirer
        l'objectif de categorie hors de portee des autres comptes.
        """
        rows = [("Mode", "Gros", 1000.0) for _ in range(200)]
        rows += [("Mode", f"Petit{i}", 100.0) for i in range(3) for _ in range(20)]
        result = build_sector_objectives(self._frame(rows), settings)
        row = result.iloc[0]
        assert row["Statut"] == "Objectifs calcules"
        # La mediane inter-comptes vaut 100 (3 petits contre 1 gros), pas ~1000
        # comme le donnerait un calcul poole.
        assert row["Niveau 1"] == 100.0

    def test_categorie_trop_petite_ne_produit_pas_de_seuil(self, settings):
        rows = [("Mode", "A", 100.0) for _ in range(30)]
        result = build_sector_objectives(self._frame(rows), settings)
        assert result.iloc[0]["Statut"] == "Echantillon sectoriel insuffisant"
        assert result.iloc[0]["Niveau 1"] is None

    def test_comptes_heterogenes_sont_signales(self, settings):
        rows = [("Mode", "Fort", 3000.0) for _ in range(20)]
        rows += [("Mode", "Moyen", 300.0) for _ in range(20)]
        rows += [("Mode", "Faible", 30.0) for _ in range(20)]
        result = build_sector_objectives(self._frame(rows), settings)
        assert "heterogenes" in result.iloc[0]["Avertissement"]

    def test_paliers_strictement_croissants(self, settings):
        rows = [("Mode", f"C{i}", 100.0) for i in range(4) for _ in range(20)]
        row = build_sector_objectives(self._frame(rows), settings).iloc[0]
        assert row["Niveau 1"] < row["Niveau 2"] < row["Niveau 3"]

    def test_colonnes_manquantes_levent_une_erreur_explicite(self, settings):
        with pytest.raises(KeyError, match="Colonnes manquantes"):
            build_sector_objectives(pd.DataFrame({"sector": ["A"]}), settings)

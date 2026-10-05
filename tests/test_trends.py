"""Tests de l'analyse des tendances."""

from __future__ import annotations

import pandas as pd
from helpers import make_posts

from socialstats.trends import (
    DECLINE,
    INSUFFICIENT,
    PROGRESSION,
    STABLE,
    add_relative_trend,
    analyze_series,
    analyze_trends,
    monthly_evolution,
)


class TestAnalyzeSeries:
    def test_progression_nette(self, settings):
        result = analyze_series(pd.Series([float(i) for i in range(50, 110, 2)]), settings)
        assert result["Tendance"] == PROGRESSION
        assert result["Variation (%)"] > 15

    def test_baisse_nette(self, settings):
        result = analyze_series(pd.Series([float(i) for i in range(110, 50, -2)]), settings)
        assert result["Tendance"] == DECLINE

    def test_serie_plate_est_stable(self, settings):
        result = analyze_series(pd.Series([100.0] * 30), settings)
        assert result["Tendance"] == STABLE

    def test_echantillon_insuffisant(self, settings):
        result = analyze_series(pd.Series([1.0, 2.0, 3.0]), settings)
        assert result["Tendance"] == INSUFFICIENT
        assert result["Variation (%)"] is None

    def test_pic_isole_ne_cree_pas_une_fausse_progression(self, settings):
        """Les medianes de segments protegent d'une viralite ponctuelle."""
        values = [100.0] * 29 + [100000.0]
        result = analyze_series(pd.Series(values), settings)
        assert result["Tendance"] != PROGRESSION

    def test_valeurs_manquantes_ignorees(self, settings):
        result = analyze_series(pd.Series([100.0, None] * 10), settings)
        assert result["Publications"] == 10


class TestAnalyzeTrends:
    def test_une_ligne_par_compte(self, settings):
        data = pd.concat(
            [
                make_posts("A", "photo", [float(i) for i in range(20)]),
                make_posts("B", "photo", [float(i) for i in range(20)]),
            ],
            ignore_index=True,
        )
        result = analyze_trends(data, settings)
        assert len(result) == 2
        assert set(result["Compte"]) == {"A", "B"}

    def test_jeu_vide(self, settings):
        assert analyze_trends(pd.DataFrame(), settings).empty


class TestAddRelativeTrend:
    def _trends(self, variations: list[float], platform: str = "instagram") -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Compte": [f"C{i}" for i in range(len(variations))],
                "Platform": platform,
                "Variation (%)": variations,
            }
        )

    def test_baisse_generalisee_n_est_pas_une_contre_performance(self, settings):
        """Cas reel : tous les comptes Instagram reculent sur la meme periode.

        Un compte qui recule comme tout le monde doit ressortir "conforme",
        sinon on sanctionne un CM pour une baisse subie par le marche entier.
        """
        result = add_relative_trend(self._trends([-60.0, -61.0, -62.0, -64.0]), settings)
        assert (result["Tendance relative"] == "Conforme a sa plateforme").all()

    def test_compte_qui_resiste_est_distingue(self, settings):
        result = add_relative_trend(self._trends([-60.0, -61.0, -62.0, -10.0]), settings)
        assert result.iloc[-1]["Tendance relative"] == "Fait mieux que sa plateforme"

    def test_compte_qui_decroche_est_distingue(self, settings):
        result = add_relative_trend(self._trends([-60.0, -61.0, -62.0, -95.0]), settings)
        assert result.iloc[-1]["Tendance relative"] == "Fait moins bien que sa plateforme"

    def test_reference_trop_petite_est_signalee(self, settings):
        result = add_relative_trend(self._trends([-60.0, -10.0]), settings)
        assert (result["Tendance relative"] == "Reference plateforme insuffisante").all()

    def test_table_vide(self, settings):
        assert add_relative_trend(pd.DataFrame(), settings).empty


class TestMonthlyEvolution:
    def test_agrege_par_mois(self):
        data = make_posts("A", "photo", [100.0] * 70, start="2026-01-01")
        result = monthly_evolution(data)
        assert len(result) >= 2
        assert set(result.columns) >= {"Compte", "mois", "Vues medianes"}

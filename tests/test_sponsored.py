"""Tests de la detection du sponsorise et de la resolution des vues organiques."""

from __future__ import annotations

import io

import pandas as pd

from socialstats.sponsored import detect_sponsored, resolve_organic_views


def frame(**columns: list) -> pd.DataFrame:
    return pd.DataFrame(columns)


class TestDetectSponsored:
    def test_detecte_via_vues_boostees(self):
        """Signal principal sur les donnees reelles.

        Sur les trois exports analyses, la colonne "Statut du contenu finance"
        est vide a 100 % alors que des publications ont des vues boostees : une
        detection reposant sur ce seul statut manquerait tout.
        """
        data = frame(views_boosted=[0.0, 8863.0], views_total=[100.0, 9251.0])
        result, report = detect_sponsored(data)
        assert result["is_sponsored"].tolist() == [False, True]
        assert report.n_sponsored == 1

    def test_detecte_via_impressions_publicitaires(self):
        data = frame(ad_impressions=[0.0, 500.0], views_total=[100.0, 200.0])
        result, _ = detect_sponsored(data)
        assert result["is_sponsored"].tolist() == [False, True]

    def test_statut_vide_ne_declenche_pas(self):
        data = frame(sponsored_status=[None, None], views_total=[100.0, 200.0])
        result, report = detect_sponsored(data)
        assert not result["is_sponsored"].any()
        assert report.n_sponsored == 0

    def test_statut_vide_lu_depuis_un_export_ne_declenche_pas(self):
        """Cas reel : la colonne existe, toutes ses cases sont vides.

        pandas 2 changeait une case vide en "nan" a la conversion en texte ;
        pandas 3 la laisse vide. Sans precaution, tout passait pour sponsorise
        et il ne restait aucune publication organique.
        """
        data = pd.read_csv(io.StringIO("sponsored_status,views_total\n,100\n,200\n"), dtype=str)
        result, report = detect_sponsored(data)
        assert not result["is_sponsored"].any()
        assert report.n_sponsored == 0

    def test_motif_est_trace(self):
        data = frame(views_boosted=[500.0], views_total=[900.0])
        result, _ = detect_sponsored(data)
        assert "boostees" in result["sponsored_reason"].iloc[0]

    def test_colonnes_absentes_sont_tolerees(self):
        # Un export a 32 colonnes ne contient aucun signal publicitaire.
        result, report = detect_sponsored(frame(views_total=[100.0, 200.0]))
        assert not result["is_sponsored"].any()
        assert report.n_total == 2


class TestResolveOrganicViews:
    def test_utilise_la_colonne_dediee(self):
        data = frame(views_organic=[80.0], views_total=[100.0], is_sponsored=[False])
        result = resolve_organic_views(data)
        assert result["views_organic_resolved"].iloc[0] == 80.0

    def test_repli_sur_vues_totales_si_non_sponsorise(self):
        """Indispensable : l'export a 32 colonnes n'a aucune ventilation.

        Sans ce repli, 224 publications parfaitement exploitables seraient perdues.
        """
        data = frame(views_organic=[None], views_total=[150.0], is_sponsored=[False])
        result = resolve_organic_views(data)
        assert result["views_organic_resolved"].iloc[0] == 150.0
        assert "non sponsorisee" in result["views_source"].iloc[0]

    def test_sponsorise_sans_detail_reste_indisponible(self):
        """Ne jamais requalifier une publication payante en organique."""
        data = frame(views_organic=[None], views_total=[5000.0], is_sponsored=[True])
        result = resolve_organic_views(data)
        assert pd.isna(result["views_organic_resolved"].iloc[0])

    def test_sponsorise_avec_detail_garde_la_part_organique(self):
        data = frame(views_organic=[388.0], views_total=[9251.0], is_sponsored=[True])
        result = resolve_organic_views(data)
        assert result["views_organic_resolved"].iloc[0] == 388.0

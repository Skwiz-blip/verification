"""Tests de l'ajout d'un export depuis l'interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from socialstats.config import load_settings
from socialstats.ingest import ADDED, DUPLICATE, REJECTED, import_export

from helpers import make_export_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ingest_settings(settings):
    """Configuration de test, avec le vrai dictionnaire de colonnes du projet."""
    real = load_settings(PROJECT_ROOT / "config")
    settings.column_mappings = real.column_mappings
    settings.format_aliases = real.format_aliases
    return settings


class TestImportExport:
    def test_un_export_reconnu_est_range_dans_les_donnees_brutes(self, ingest_settings):
        outcome = import_export("juin.csv", make_export_csv(n=5).encode("utf-8"), ingest_settings)
        assert outcome.status == ADDED
        assert outcome.path == ingest_settings.raw_dir / "juin.csv"
        assert outcome.path.exists()
        assert outcome.stats["publications"] == 5
        assert outcome.stats["platform"] == "facebook"
        assert (outcome.stats["start"], outcome.stats["end"]) == ("2026-06-01", "2026-06-05")

    def test_le_meme_contenu_n_est_pas_ajoute_deux_fois(self, ingest_settings):
        content = make_export_csv(n=5).encode("utf-8")
        import_export("juin.csv", content, ingest_settings)
        outcome = import_export("copie.csv", content, ingest_settings)
        assert outcome.status == DUPLICATE
        assert [p.name for p in ingest_settings.raw_dir.iterdir()] == ["juin.csv"]

    def test_un_fichier_existant_n_est_jamais_ecrase(self, ingest_settings):
        first = make_export_csv(n=5).encode("utf-8")
        import_export("juin.csv", first, ingest_settings)
        outcome = import_export("juin.csv", make_export_csv(n=6).encode("utf-8"), ingest_settings)
        assert outcome.status == ADDED
        assert outcome.path.name == "juin_2.csv"
        assert (ingest_settings.raw_dir / "juin.csv").read_bytes() == first

    def test_un_format_inconnu_est_refuse_sans_rien_ecrire(self, ingest_settings):
        outcome = import_export("autre.csv", b"a,b\n1,2\n", ingest_settings)
        assert outcome.status == REJECTED
        assert list(ingest_settings.raw_dir.iterdir()) == []

    def test_un_fichier_non_csv_est_refuse(self, ingest_settings):
        assert import_export("note.txt", b"bonjour", ingest_settings).status == REJECTED

    def test_le_nom_ne_peut_pas_sortir_du_dossier(self, ingest_settings):
        outcome = import_export(
            "../../ailleurs.csv", make_export_csv(n=5).encode("utf-8"), ingest_settings
        )
        assert outcome.path == ingest_settings.raw_dir / "ailleurs.csv"
        assert not (ingest_settings.project_root.parent / "ailleurs.csv").exists()

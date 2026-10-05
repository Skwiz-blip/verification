"""Tests du mapping de colonnes et de la detection de format."""

from __future__ import annotations

import pandas as pd
import pytest

from socialstats.schema_mapper import (
    SchemaError,
    build_column_map,
    detect_platform,
    display_account_name,
    normalize,
    normalize_account_name,
    parse_dates,
    resolve_format,
)

MAPPINGS = {
    "fields": {
        "post_id": {"exact": ["identifiant de la publication"], "required": True},
        "account_name": {"exact": ["nom de la page"], "required": True},
        "views_organic": {
            "exact": ["vues de publications organiques"],
            "patterns": ["^vues de publications organiques$"],
        },
        "views_total": {"exact": ["vues"]},
        "followers": {"exact": ["abonnes"]},
    }
}


class TestNormalize:
    def test_supprime_accents_et_casse(self):
        assert normalize("Réactions") == "reactions"
        assert normalize("VUES") == "vues"

    def test_unifie_apostrophe_typographique(self):
        # Les exports Meta utilisent l'apostrophe courbe U+2019.
        assert normalize("Vues de vidéo d’une minute") == normalize("Vues de video d'une minute")

    def test_unifie_espace_insecable(self):
        # "Spectateurs de 3\xa0secondes" apparait tel quel dans les exports reels.
        assert normalize("Spectateurs de 3\xa0secondes") == "spectateurs de 3 secondes"


class TestBuildColumnMap:
    def test_mappe_les_colonnes_presentes(self):
        columns = ["Identifiant de la publication", "Nom de la page", "Vues"]
        resolved, missing, _ = build_column_map(columns, MAPPINGS)
        assert resolved["post_id"] == ["Identifiant de la publication"]
        assert resolved["views_total"] == ["Vues"]
        assert "followers" in missing

    def test_ignore_les_colonnes_inutiles(self):
        columns = ["Vues", "Revenus estimés (USD)", "Langues", "Permalien"]
        resolved, _, _ = build_column_map(columns, MAPPINGS)
        used = {col for sources in resolved.values() for col in sources}
        assert used == {"Vues"}

    def test_pattern_ne_capture_pas_une_colonne_voisine(self):
        """Garde-fou central : ne pas confondre vues organiques et vues video.

        Le fichier reel a 227 colonnes contient
        "Vues de vidéo de 3 secondes de Publications organiques", qui ne doit
        jamais etre pris pour la metrique de vues organiques.
        """
        columns = [
            "Vues de vidéo de 3 secondes de Publications organiques",
            "Vues de Publications organiques",
        ]
        resolved, _, _ = build_column_map(columns, MAPPINGS)
        assert resolved["views_organic"] == ["Vues de Publications organiques"]

    def test_une_colonne_n_est_pas_attribuee_deux_fois(self):
        resolved, _, _ = build_column_map(["Vues"], MAPPINGS)
        used = [col for sources in resolved.values() for col in sources]
        assert len(set(used)) == len(used)

    def test_colonnes_bilingues_fusionnees_par_priorite(self):
        """Exports bilingues : la colonne francaise prime, l'anglaise complete."""
        mappings = {"fields": {"post_type_raw": {"exact": ["type de publication", "post type"]}}}
        resolved, _, ambiguous = build_column_map(["Post type", "Type de publication"], mappings)
        assert resolved["post_type_raw"] == ["Type de publication", "Post type"]
        assert "post_type_raw" in ambiguous


class TestNormalizeAccountName:
    def test_lettres_cerclees_instagram(self):
        # "Ⓐ Ⓑ Ⓘ 'Ⓢ Ⓒ Ⓡ Ⓔ Ⓐ Ⓜ" = ABI'S CREAM, ecrit lettre par lettre.
        assert normalize_account_name("Ⓐ Ⓑ Ⓘ 'Ⓢ   Ⓒ Ⓡ Ⓔ Ⓐ Ⓜ") == "abi'scream"

    def test_gras_mathematique(self):
        assert normalize_account_name("𝗖𝗟𝗔𝗜𝗥 𝗢𝗣𝗧𝗜𝗖 𝗧𝗢𝗚𝗢") == "clair optic togo"

    def test_emojis_retires(self):
        assert normalize_account_name("KRYSTAL OPTIQUE 🇹🇬🇧🇯") == "krystal optique"

    def test_meme_compte_ecrit_differemment_selon_la_plateforme(self):
        assert normalize_account_name("DAGAN MAGAZINE") == normalize_account_name("Dagan Magazine")

    def test_comptes_distincts_restent_distincts(self):
        # Togo et Benin sont deux comptes differents du meme client.
        assert normalize_account_name("KRYSTAL OPTIQUE 🇹🇬") != normalize_account_name(
            "Krystal Optique Benin"
        )


class TestDisplayAccountName:
    def test_nom_stylise_devient_lisible(self):
        # matplotlib ne dispose d'aucune police pour ces caracteres.
        assert display_account_name("𝗖𝗟𝗔𝗜𝗥 𝗢𝗣𝗧𝗜𝗖 𝗧𝗢𝗚𝗢") == "Clair Optic Togo"

    def test_nom_deja_lisible_est_intact(self):
        assert display_account_name("Dom's Restaurant") == "Dom's Restaurant"


class TestDetectPlatform:
    def test_reconnait_facebook(self):
        columns = ["ID de la Page", "Nom de la page", "Est un crosspostage"]
        assert detect_platform(columns) == "facebook"

    def test_reconnait_instagram(self):
        columns = ["ID du compte", "Nom du compte", "Enregistrements", "Mentions J’aime"]
        assert detect_platform(columns) == "instagram"

    def test_rejette_une_signature_inconnue(self):
        # Mieux vaut une erreur explicite qu'une plateforme devinee au hasard.
        with pytest.raises(SchemaError):
            detect_platform(["Colonne X", "Colonne Y"])

    def test_repli_sur_le_permalien(self):
        permalinks = pd.Series(["https://www.facebook.com/page/posts/123"])
        assert detect_platform(["Colonne X"], permalinks) == "facebook"


class TestParseDates:
    def test_format_americain_des_exports_meta(self):
        result = parse_dates(pd.Series(["05/28/2026 05:06"]), ["%m/%d/%Y %H:%M"])
        assert result.iloc[0] == pd.Timestamp("2026-05-28 05:06")

    def test_valeur_illisible_devient_nat(self):
        result = parse_dates(pd.Series(["Global"]), ["%m/%d/%Y %H:%M"])
        assert pd.isna(result.iloc[0])


class TestResolveFormat:
    ALIASES = {"photos": "photo", "videos": "video", "reels": "reel"}

    def test_mappe_le_type_declare(self):
        result = resolve_format(
            pd.Series(["Photos", "Reels"]), None, self.ALIASES, detect_reel_from_permalink=False
        )
        assert result.tolist() == ["photo", "reel"]

    def test_reel_detecte_via_permalien_malgre_type_video(self):
        """Cas reel des exports 2026 : les Reels y sont etiquetes "Videos".

        Sans cette correction, 41 Reels seraient compares a des videos classiques
        et fausseraient les objectifs.
        """
        result = resolve_format(
            pd.Series(["Vidéos"]),
            pd.Series(["https://www.facebook.com/reel/1334347375275887/"]),
            self.ALIASES,
            detect_reel_from_permalink=True,
        )
        assert result.iloc[0] == "reel"

    def test_video_classique_reste_video(self):
        result = resolve_format(
            pd.Series(["Vidéos"]),
            pd.Series(["https://www.facebook.com/page/videos/113553271/"]),
            self.ALIASES,
            detect_reel_from_permalink=True,
        )
        assert result.iloc[0] == "video"

    def test_type_inconnu_devient_other(self):
        result = resolve_format(
            pd.Series(["Format Inedit"]), None, self.ALIASES, detect_reel_from_permalink=False
        )
        assert result.iloc[0] == "other"

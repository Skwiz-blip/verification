"""Tests de la publication vers Supabase : cles, lignes envoyees, appels REST."""

from __future__ import annotations

import base64
import json
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

from socialstats import publish as publish_module
from socialstats.cleaner import CleaningResult
from socialstats.publish import (
    PUBLISHED_PRESETS,
    ROLE_PUBLIC,
    ROLE_SECRET,
    ROLE_UNKNOWN,
    PublishError,
    SupabaseClient,
    check_keys,
    control_rows,
    key_role,
    load_env,
    publication_rows,
    publish,
    state_row,
    write_web_config,
)

from helpers import make_posts

STAMP = "2026-10-05T19:40:00+00:00"
URL = "https://exemple.supabase.co"


def jwt(role: str) -> str:
    """Jeton au format des anciennes cles Supabase (signature factice)."""
    body = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.signature"


@pytest.fixture
def cleaning() -> CleaningResult:
    data = pd.concat(
        [
            make_posts("Compte A", "photo", [100.0 + i for i in range(60)], start="2026-01-01"),
            make_posts("Compte A", "photo", [200.0] * 5, start="2026-04-01"),
        ],
        ignore_index=True,
    )
    data["post_id"] = [f"post-{i}" for i in range(len(data))]
    data["permalink"] = "https://www.facebook.com/page/posts/1"
    data.loc[0, "permalink"] = "javascript:alert(1)"
    data["description"] = "x" * 500
    return CleaningResult(data=data, sponsored_data=data.iloc[0:0], rejected_data=data.iloc[0:0])


class Recorder:
    """Remplace le reseau : retient chaque requete au lieu de l'envoyer."""

    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return b""

    def calls(self) -> list[tuple[str, str]]:
        return [(r.get_method(), r.full_url.replace(URL, "")) for r in self.requests]


class TestKeys:
    def test_role_des_cles_recentes(self):
        assert key_role("sb_secret_abc") == ROLE_SECRET
        assert key_role("sb_publishable_abc") == ROLE_PUBLIC

    def test_role_des_anciennes_cles(self):
        assert key_role(jwt("service_role")) == ROLE_SECRET
        assert key_role(jwt("anon")) == ROLE_PUBLIC

    def test_cle_illisible(self):
        assert key_role("n'importe quoi") == ROLE_UNKNOWN
        assert key_role("a.b.c") == ROLE_UNKNOWN

    def test_cle_secrete_absente(self):
        with pytest.raises(PublishError, match="SUPABASE_SERVICE_ROLE_KEY"):
            check_keys(URL, "", "")

    def test_cle_publique_a_la_place_de_la_secrete(self):
        with pytest.raises(PublishError, match="cle publique"):
            check_keys(URL, jwt("anon"), "")

    def test_la_cle_secrete_ne_part_jamais_dans_l_interface(self, tmp_path):
        """Une inversion des deux cles ne doit pas rendre la cle secrete publique."""
        with pytest.raises(PublishError, match="SECRETE"):
            check_keys(URL, jwt("service_role"), jwt("service_role"))
        with pytest.raises(PublishError):
            write_web_config(tmp_path, URL, "sb_secret_abc")
        assert list(tmp_path.iterdir()) == []

    def test_cles_correctes(self):
        check_keys(URL, jwt("service_role"), jwt("anon"))

    def test_fichier_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_ANON_KEY", "depuis-l-environnement")
        path = tmp_path / ".env"
        path.write_text(
            "# commentaire\nSUPABASE_URL = \"https://exemple.supabase.co\"\n"
            "SUPABASE_ANON_KEY=depuis-le-fichier\nligne sans signe egal\n",
            encoding="utf-8",
        )
        env = load_env(path)
        assert env["SUPABASE_URL"] == URL
        assert env["SUPABASE_ANON_KEY"] == "depuis-l-environnement"
        assert load_env(tmp_path / "absent")["SUPABASE_ANON_KEY"] == "depuis-l-environnement"


class TestRows:
    def test_publications_au_schema_de_la_table(self, cleaning):
        rows = publication_rows(cleaning, STAMP)
        assert len(rows) == 65
        first = rows[0]
        assert first["post_id"] == "post-0"
        assert first["publie_le"] == "2026-01-01T00:00:00"
        assert first["mois"] == "2026-01"
        assert first["vues"] == 100.0
        assert first["maj_le"] == STAMP
        assert len(first["texte"]) == 280
        json.dumps(rows, allow_nan=False)

    def test_seuls_les_liens_web_sont_publies(self, cleaning):
        rows = publication_rows(cleaning, STAMP)
        assert rows[0]["lien"] is None
        assert rows[1]["lien"].startswith("https://")

    def test_un_identifiant_en_double_ne_part_qu_une_fois(self, cleaning):
        cleaning.data.loc[1, "post_id"] = "post-0"
        ids = [row["post_id"] for row in publication_rows(cleaning, STAMP)]
        assert len(ids) == len(set(ids)) == 64

    def test_un_controle_par_mois_et_par_reference(self, cleaning, settings):
        seen = []
        rows = control_rows(cleaning, settings, 3, STAMP, seen.append)
        assert len(rows) == 4 * len(PUBLISHED_PRESETS)
        assert [str(month) for month in seen] == ["2026-01", "2026-02", "2026-03", "2026-04"]
        assert {(row["mois"], row["ref"]) for row in rows} == {
            (month, preset)
            for month in ("2026-01", "2026-02", "2026-03", "2026-04")
            for preset in PUBLISHED_PRESETS
        }
        april = next(r for r in rows if r["mois"] == "2026-04" and r["ref"] == "before")["payload"]
        assert april["meta"]["mois_controle"] == "2026-04"
        assert april["meta"]["ref"]["fin"] == "2026-03"
        json.dumps(rows, allow_nan=False)

    def test_le_chemin_local_n_est_pas_publie(self, cleaning, settings):
        rows = control_rows(cleaning, settings, 3, STAMP)
        assert all(row["payload"]["donnees"]["dossier"] == "" for row in rows)

    def test_etat_de_la_publication(self, cleaning):
        value = state_row(cleaning, 3, STAMP)["valeur"]
        assert value["mois"] == ["2026-01", "2026-02", "2026-03", "2026-04"]
        assert value["mois_defaut"] == "2026-04"
        assert value["refs"] == list(PUBLISHED_PRESETS)
        assert value["publie_le"] == STAMP


class TestClient:
    def test_upsert_par_lots_avec_la_cle(self):
        recorder = Recorder()
        client = SupabaseClient(URL + "/", "cle-secrete", recorder)
        client.upsert("publications", [{"post_id": str(i)} for i in range(5)], "post_id", 2)

        assert recorder.calls() == [("POST", "/rest/v1/publications?on_conflict=post_id")] * 3
        first = recorder.requests[0]
        assert json.loads(first.data) == [{"post_id": "0"}, {"post_id": "1"}]
        assert first.get_header("Apikey") == "cle-secrete"
        assert first.get_header("Authorization") == "Bearer cle-secrete"
        assert "merge-duplicates" in first.get_header("Prefer")

    def test_la_date_du_filtre_est_encodee(self):
        recorder = Recorder()
        SupabaseClient(URL, "cle", recorder).delete_older("controles", STAMP)
        assert recorder.calls() == [
            ("DELETE", "/rest/v1/controles?maj_le=lt.2026-10-05T19%3A40%3A00%2B00%3A00")
        ]

    def test_archive_d_un_export(self):
        recorder = Recorder()
        SupabaseClient(URL, "cle", recorder).upload("exports", "juin 2026.csv", b"a,b")
        assert recorder.calls() == [("POST", "/storage/v1/object/exports/juin%202026.csv")]
        assert recorder.requests[0].get_header("X-upsert") == "true"

    def test_un_refus_est_explique_sans_la_cle(self):
        def refuse(request):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        client = SupabaseClient(URL, "cle-tres-secrete", refuse)
        with pytest.raises(PublishError) as error:
            client.upsert("publications", [{"post_id": "1"}], "post_id", 10)
        assert "HTTP 404" in str(error.value)
        assert "cle-tres-secrete" not in str(error.value)


class TestPublish:
    def test_les_nouvelles_lignes_arrivent_avant_le_retrait_des_anciennes(self, tmp_path):
        export = tmp_path / "juin.csv"
        export.write_bytes(b"a,b")
        recorder = Recorder()
        publish(
            SupabaseClient(URL, "cle", recorder),
            [{"post_id": "1"}],
            [{"mois": "2026-06", "ref": "before", "payload": {}}],
            {"cle": "publication", "valeur": {}},
            [export],
            STAMP,
        )
        assert [(method, path.split("?")[0]) for method, path in recorder.calls()] == [
            ("POST", "/rest/v1/publications"),
            ("POST", "/rest/v1/controles"),
            ("POST", "/rest/v1/etat"),
            ("DELETE", "/rest/v1/publications"),
            ("DELETE", "/rest/v1/controles"),
            ("POST", "/storage/v1/object/exports/juin.csv"),
        ]

    def test_configuration_de_l_interface(self, tmp_path):
        path = write_web_config(tmp_path, URL, jwt("anon"))
        content = path.read_text(encoding="utf-8")
        assert content.strip().endswith(";")
        settings = json.loads(content.split("window.CONTROLE_CONFIG = ")[1].rstrip().rstrip(";"))
        assert settings == {"supabaseUrl": URL, "supabaseKey": jwt("anon")}

    def test_sans_cle_la_commande_s_arrete_avant_tout_calcul(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(publish_module, "load_env", lambda path: {})
        monkeypatch.setattr(
            publish_module, "run_cleaning", lambda settings: pytest.fail("calcul lance sans cle")
        )
        assert publish_module.main(["--config", str(Path(tmp_path))]) == 1
        assert "SUPABASE_URL" in capsys.readouterr().err

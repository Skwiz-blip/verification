"""Listes de pages nommees, en general une par Community Manager.

Une liste n'est qu'un raccourci de selection dans l'interface : elle ne
modifie aucun calcul. En ligne, les listes vivent dans la table `listes` de
Supabase ; pour l'interface locale, dans un fichier JSON a cote des exports.
Les regles sont les memes des deux cotes (voir supabase/schema.sql) : un nom
de 1 a 80 caracteres, unique sans tenir compte de la casse, et au plus 300
pages.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_NAME = 80
MAX_ACCOUNTS = 300
MAX_ACCOUNT_NAME = 200


class PageListError(ValueError):
    """Liste refusee ou illisible ; le message s'adresse a l'utilisateur."""


def clean_list(payload: object) -> tuple[str | None, str, list[str]]:
    """Identifiant, nom et pages d'une liste envoyee par le navigateur, verifies."""
    if not isinstance(payload, dict):
        raise PageListError("Liste illisible.")
    list_id = payload.get("id")
    if list_id is not None and not isinstance(list_id, str):
        raise PageListError("Identifiant de liste illisible.")
    name = payload.get("nom")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME:
        raise PageListError(f"Le nom de la liste doit faire de 1 a {MAX_NAME} caracteres.")
    accounts = payload.get("comptes")
    if not isinstance(accounts, list) or not all(
        isinstance(a, str) and 1 <= len(a) <= MAX_ACCOUNT_NAME for a in accounts
    ):
        raise PageListError("Pages de la liste illisibles.")
    accounts = sorted(set(accounts), key=str.casefold)
    if len(accounts) > MAX_ACCOUNTS:
        raise PageListError(f"Une liste compte au plus {MAX_ACCOUNTS} pages.")
    return list_id, name.strip(), accounts


class PageListStore:
    """Listes rangees dans un fichier JSON, ecrit d'un bloc a chaque modification."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            lists = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Repartir d'une liste vide effacerait celles du fichier au prochain enregistrement.
            raise PageListError(f"Fichier des listes illisible ({self.path}) : {exc}") from exc
        if not isinstance(lists, list):
            raise PageListError(f"Fichier des listes illisible ({self.path}).")
        return lists

    def _write(self, lists: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        draft = self.path.with_name(f"{self.path.name}.tmp")
        draft.write_text(json.dumps(lists, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(draft, self.path)

    def all(self) -> list[dict]:
        with self._lock:
            return sorted(self._read(), key=lambda item: str(item.get("nom", "")).casefold())

    def save(self, payload: object) -> dict:
        """Cree la liste, ou la remplace si elle porte un identifiant connu."""
        list_id, name, accounts = clean_list(payload)
        with self._lock:
            lists = self._read()
            if any(item["nom"].casefold() == name.casefold() and item["id"] != list_id for item in lists):
                raise PageListError("Une liste porte deja ce nom.")
            if list_id is not None and not any(item["id"] == list_id for item in lists):
                raise PageListError("Cette liste n'existe plus : elle a ete supprimee entre-temps.")
            saved = {
                "id": list_id or str(uuid.uuid4()),
                "nom": name,
                "comptes": accounts,
                "maj_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._write([item for item in lists if item["id"] != saved["id"]] + [saved])
            return saved

    def remove(self, list_id: object) -> None:
        """Supprime une liste ; une liste deja absente n'est pas une erreur."""
        with self._lock:
            lists = self._read()
            kept = [item for item in lists if item["id"] != list_id]
            if len(kept) != len(lists):
                self._write(kept)

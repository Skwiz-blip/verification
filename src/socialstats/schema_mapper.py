"""Mapping des colonnes brutes vers un schema canonique stable.

Point cle du projet : les exports Meta observes vont de 32 a 227 colonnes, avec
des libelles variables. Le mapping repose donc sur une normalisation des noms
(accents, casse, apostrophes typographiques, espaces insecables) puis sur une
correspondance EXACTE prioritaire, les expressions regulieres n'intervenant
qu'en second recours.

Ce choix est deliberement conservateur : sur le fichier a 227 colonnes, un motif
large comme ".*organique.*" capturerait a tort
"Vues de video de 3 secondes de Publications organiques".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

PLATFORM_FACEBOOK = "facebook"
PLATFORM_INSTAGRAM = "instagram"

# Marqueurs de colonnes propres aux exports Facebook "publications".
FACEBOOK_COLUMN_MARKERS = {
    "id de la page",
    "nom de la page",
    "est un crosspostage",
    "vues de publications organiques",
    "couverture de publications organiques",
}
# Marqueurs Instagram, valides sur des exports IG reels (18 a 23 colonnes).
# Les exports IG n'ont ni "Reactions" ni ventilation organique/boostee : ils
# exposent "Mentions J'aime", "Enregistrements" et "Followers en plus".
INSTAGRAM_COLUMN_MARKERS = {
    "identifiant du compte",
    "id du compte",
    "nom du compte",
    "nom de profil du compte",
    "enregistrements",
    "mentions j'aime",
    "followers en plus",
    "visites du profil",
}

FORMAT_OTHER = "other"
FORMAT_REEL = "reel"

# Un permalien /reel/ est le seul signal fiable pour distinguer un Reel dans les
# exports recents, ou le "Type de publication" les etiquette "Videos".
REEL_PERMALINK = re.compile(r"facebook\.com/reel/|/reels?/", re.IGNORECASE)


class SchemaError(Exception):
    """Le fichier ne correspond a aucun format d'export supporte."""


def normalize(name: Any) -> str:
    """Normalise un nom de colonne pour une comparaison insensible aux variantes.

    Supprime les accents, unifie l'apostrophe typographique et l'espace
    insecable (tous deux presents dans les exports Meta), passe en minuscules.
    """
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_account_name(name: Any) -> str:
    """Cle de rapprochement d'un compte, insensible au style d'ecriture.

    Les noms Instagram sont frequemment ecrits en caracteres Unicode stylises :
    "𝗖𝗟𝗔𝗜𝗥 𝗢𝗣𝗧𝗜𝗖 𝗧𝗢𝗚𝗢" ou "Ⓐ Ⓑ Ⓘ 'Ⓢ Ⓒ Ⓡ Ⓔ Ⓐ Ⓜ" (= ABI'S CREAM). La
    decomposition NFKD les ramene a des lettres ASCII ; le cas des lettres
    isolees separees par des espaces est recolle ensuite.

    Les emojis et la ponctuation decorative sont retires afin que
    "KRYSTAL OPTIQUE 🇹🇬🇧🇯" et "Krystal Optique" se rejoignent.
    """
    text = normalize(name)
    # Retire tout ce qui n'est ni lettre, ni chiffre, ni apostrophe, ni espace.
    text = "".join(
        ch if (ch.isalnum() or ch in " '") else " " for ch in text
    )
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    tokens = text.split(" ")
    # "a b i 's c r e a m" -> "abi's cream" : si la majorite des fragments sont
    # des lettres isolees, le nom a ete ecrit lettre par lettre.
    singles = sum(1 for token in tokens if len(token.replace("'", "")) <= 1)
    if len(tokens) > 3 and singles >= len(tokens) * 0.6:
        text = "".join(tokens)
        text = re.sub(r"\s+", " ", text).strip()
    return text


def display_account_name(name: Any) -> str:
    """Libelle affichable d'un compte, sur les graphiques et dans les rapports.

    Les noms Instagram ecrits en Unicode stylise (lettres cerclees, gras
    mathematique) n'existent dans aucune police courante : matplotlib les rend
    en carres vides. On leur substitue leur equivalent ASCII, tout en laissant
    intacts les noms deja lisibles.
    """
    raw = str(name).strip()
    # La ponctuation typographique est simplement transcrite : elle ne justifie
    # pas de reecrire tout le nom.
    for fancy, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-")):
        raw = raw.replace(fancy, plain)
    try:
        raw.encode("latin-1")
    except UnicodeEncodeError:
        cleaned = normalize_account_name(raw)
        return " ".join(word.capitalize() for word in cleaned.split(" ")) if cleaned else raw
    return raw


@dataclass
class MappingReport:
    """Trace de ce qui a ete reconnu dans un fichier, pour audit."""

    file_name: str
    platform: str
    n_source_columns: int
    mapped: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n_used_columns(self) -> int:
        return sum(len(sources) for sources in self.mapped.values())

    @property
    def n_ignored_columns(self) -> int:
        return self.n_source_columns - self.n_used_columns

    def as_row(self) -> dict[str, Any]:
        return {
            "fichier": self.file_name,
            "plateforme": self.platform,
            "colonnes_source": self.n_source_columns,
            "colonnes_mappees": self.n_used_columns,
            "colonnes_ignorees": self.n_ignored_columns,
            "champs_absents": ", ".join(self.missing) if self.missing else "",
            "ambiguites": ", ".join(self.ambiguous) if self.ambiguous else "",
        }


def detect_platform(columns: list[str], permalinks: pd.Series | None = None) -> str:
    """Determine la plateforme a partir de la signature des colonnes.

    Aucune supposition n'est faite : si la signature est inconnue, une erreur
    explicite est levee plutot que de classer le fichier au hasard.
    """
    normalized = {normalize(c) for c in columns}
    fb_score = len(normalized & FACEBOOK_COLUMN_MARKERS)
    ig_score = len(normalized & INSTAGRAM_COLUMN_MARKERS)

    if fb_score > ig_score and fb_score >= 2:
        return PLATFORM_FACEBOOK
    if ig_score > fb_score and ig_score >= 2:
        return PLATFORM_INSTAGRAM

    if permalinks is not None and not permalinks.empty:
        sample = permalinks.dropna().astype(str).str.lower()
        if sample.str.contains("instagram.com").any():
            return PLATFORM_INSTAGRAM
        if sample.str.contains("facebook.com").any():
            return PLATFORM_FACEBOOK

    raise SchemaError(
        "Signature de colonnes inconnue : impossible de determiner la plateforme "
        "de maniere fiable. Ajoutez les libelles de ce format dans "
        "config/column_mappings.yaml avant de l'analyser."
    )


def _resolve_field(
    field_name: str,
    spec: dict[str, Any],
    norm_to_original: dict[str, str],
    already_used: set[str],
) -> list[str]:
    """Trouve TOUTES les colonnes sources d'un champ, par ordre de priorite.

    Retourner plusieurs colonnes permet de fusionner les exports bilingues, ou
    une meme information est repartie entre une colonne francaise et une
    colonne anglaise (ex. "Type de publication" et "Post type", chacune
    renseignee sur une partie des lignes seulement).
    """
    found: list[str] = []
    for candidate in spec.get("exact", []) or []:
        key = normalize(candidate)
        original = norm_to_original.get(key)
        if original and original not in already_used and original not in found:
            found.append(original)

    if found:
        return found

    matches: list[str] = []
    for pattern in spec.get("patterns", []) or []:
        regex = re.compile(pattern)
        for norm_name, original in norm_to_original.items():
            if original in already_used:
                continue
            if regex.search(norm_name) and original not in matches:
                matches.append(original)

    if len(matches) > 1:
        logger.warning(
            "Champ %s : %d colonnes candidates (%s). Fusionnees par ordre de priorite.",
            field_name,
            len(matches),
            ", ".join(matches[:3]),
        )
    return matches


def build_column_map(
    columns: list[str], mappings: dict[str, Any]
) -> tuple[dict[str, list[str]], list[str], dict[str, list[str]]]:
    """Associe chaque champ canonique a ses colonnes sources, par priorite.

    Retourne (champ -> colonnes sources, champs absents, champs multi-colonnes).
    """
    norm_to_original: dict[str, str] = {}
    for column in columns:
        key = normalize(column)
        # En cas de doublon de nom normalise, la premiere occurrence prime.
        norm_to_original.setdefault(key, column)

    field_specs: dict[str, Any] = mappings.get("fields", {})
    resolved: dict[str, list[str]] = {}
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    used: set[str] = set()

    for field_name, spec in field_specs.items():
        spec = spec or {}
        sources = _resolve_field(field_name, spec, norm_to_original, used)
        if not sources:
            missing.append(field_name)
            if spec.get("required"):
                logger.warning("Champ requis absent du fichier : %s", field_name)
            continue
        resolved[field_name] = sources
        used.update(sources)
        if len(sources) > 1:
            ambiguous[field_name] = sources

    return resolved, missing, ambiguous


def parse_dates(series: pd.Series, formats: list[str]) -> pd.Series:
    """Parse une colonne de dates en testant les formats connus dans l'ordre.

    Les exports Meta utilisent MM/DD/YYYY HH:MM. On ne se repose sur l'inference
    pandas qu'en dernier recours, car elle peut inverser jour et mois.
    """
    cleaned = series.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    for fmt in formats:
        pending = result.isna() & cleaned.notna()
        if not pending.any():
            break
        attempt = pd.to_datetime(cleaned[pending], format=fmt, errors="coerce")
        result.loc[pending] = attempt

    pending = result.isna()
    if pending.any():
        attempt = pd.to_datetime(cleaned[pending], errors="coerce")
        result.loc[pending] = attempt
    return result


def resolve_format(
    post_type: pd.Series,
    permalink: pd.Series | None,
    aliases: dict[str, str],
    detect_reel_from_permalink: bool,
) -> pd.Series:
    """Deduit le format canonique de chaque publication.

    Le "Type de publication" seul est insuffisant : dans les exports 2026
    observes, les Reels sont etiquetes "Videos" et seul le permalien /reel/
    permet de les distinguer. Les confondre fausserait les objectifs, un Reel
    generant un ordre de grandeur de vues different d'une video classique.
    """
    normalized = post_type.astype(str).map(normalize)
    formats = normalized.map(lambda v: aliases.get(v, FORMAT_OTHER))

    if detect_reel_from_permalink and permalink is not None:
        is_reel = permalink.fillna("").astype(str).str.contains(REEL_PERMALINK, regex=True)
        reclassified = int((is_reel & (formats != FORMAT_REEL)).sum())
        if reclassified:
            logger.info(
                "%d publication(s) reclassee(s) en Reel d'apres le permalien "
                "(type Meta declare : %s).",
                reclassified,
                ", ".join(sorted(set(post_type[is_reel & (formats != FORMAT_REEL)].dropna()))),
            )
        formats = formats.mask(is_reel, FORMAT_REEL)

    unknown = normalized[formats == FORMAT_OTHER].unique().tolist()
    if unknown:
        logger.warning(
            "Type(s) de publication non mappe(s) vers un format connu : %s "
            "(ajoutez-les dans settings.yaml > format_aliases).",
            ", ".join(str(u) for u in unknown[:5]),
        )
    return formats


def map_frame(
    frame: pd.DataFrame,
    file_name: str,
    mappings: dict[str, Any],
    format_aliases: dict[str, str],
    detect_reel_from_permalink: bool = True,
) -> tuple[pd.DataFrame, MappingReport]:
    """Transforme un CSV brut en DataFrame au schema canonique."""
    columns = list(frame.columns)
    permalink_col = next(
        (c for c in columns if normalize(c) in {"permalien", "permalink"}), None
    )
    platform = detect_platform(columns, frame[permalink_col] if permalink_col else None)

    resolved, missing, ambiguous = build_column_map(columns, mappings)
    report = MappingReport(
        file_name=file_name,
        platform=platform,
        n_source_columns=len(columns),
        mapped=resolved,
        missing=missing,
        ambiguous=ambiguous,
    )

    for required in ("post_id", "account_name", "published_at"):
        if required not in resolved:
            raise SchemaError(
                f"{file_name} : champ requis '{required}' introuvable. "
                f"Colonnes disponibles (extrait) : {columns[:8]}"
            )

    null_tokens = set(mappings.get("null_tokens", []) or [])

    out = pd.DataFrame(index=frame.index)
    for field_name, sources in resolved.items():
        # Fusion par priorite : la premiere colonne renseignee gagne. Necessaire
        # pour les exports bilingues, ou une partie des lignes n'est decrite que
        # par la colonne anglaise et le reste par la colonne francaise.
        series = frame[sources[0]]
        if series.dtype == object:
            series = series.where(~series.isin(null_tokens))
        for extra in sources[1:]:
            fallback = frame[extra]
            if fallback.dtype == object:
                fallback = fallback.where(~fallback.isin(null_tokens))
            filled = int(series.isna().sum() - series.fillna(fallback).isna().sum())
            if filled:
                logger.info(
                    "%s : champ %s complete sur %d ligne(s) via la colonne '%s'.",
                    file_name,
                    field_name,
                    filled,
                    extra,
                )
            series = series.fillna(fallback)
        out[field_name] = series

    for field_name in mappings.get("numeric_fields", []) or []:
        if field_name in out.columns:
            out[field_name] = pd.to_numeric(
                out[field_name].astype(str).str.replace(r"[\s ]", "", regex=True),
                errors="coerce",
            )
        else:
            # Champ absent de cet export : colonne flottante vide, pour que la
            # concatenation avec des fichiers plus riches garde le bon dtype.
            out[field_name] = pd.Series(float("nan"), index=out.index, dtype="float64")

    out["published_at"] = parse_dates(out["published_at"], mappings.get("date_formats", []))
    unparsed = int(out["published_at"].isna().sum())
    if unparsed:
        logger.warning("%s : %d date(s) de publication non interpretable(s).", file_name, unparsed)

    out["platform"] = platform
    out["format"] = resolve_format(
        out["post_type_raw"] if "post_type_raw" in out.columns else pd.Series("", index=out.index),
        out["permalink"] if "permalink" in out.columns else None,
        format_aliases,
        detect_reel_from_permalink,
    )
    out["account_name"] = out["account_name"].astype(str).str.strip()
    out["post_id"] = out["post_id"].astype(str).str.strip()
    out["source_file"] = file_name

    logger.info(
        "%s : %d/%d colonnes mappees, plateforme=%s, %d champ(s) canonique(s) absent(s).",
        file_name,
        len(resolved),
        len(columns),
        platform,
        len(missing),
    )
    return out, report

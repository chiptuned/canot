"""Proof levels: what a comparison of Canot actually established, as a stable machine identifier, in one place.

Each table (canot drill) or model (dbt replay) gets one level; an exercise gets the LOWEST level over its tables, never an
average. Reserved levels are defined so that reports and readers know the scale, but nothing produces them yet: only
`full_aggregates`, `metadata_only` and `not_compared` can be produced today.
"""
from __future__ import annotations

from collections.abc import Iterable

FULL_FINGERPRINT = "full_fingerprint"
FULL_AGGREGATES = "full_aggregates"
PARTITION_SAMPLED = "partition_sampled"
ROW_SAMPLED = "row_sampled"
METADATA_ONLY = "metadata_only"
NOT_COMPARED = "not_compared"

# weakest first: the exercise level is the first of these that one of its tables has
ORDER = (NOT_COMPARED, METADATA_ONLY, ROW_SAMPLED, PARTITION_SAMPLED, FULL_AGGREGATES, FULL_FINGERPRINT)
RESERVED = frozenset({FULL_FINGERPRINT, PARTITION_SAMPLED, ROW_SAMPLED})  # defined, not implemented: never produced
PRODUCED = (FULL_AGGREGATES, METADATA_ONLY, NOT_COMPARED)

LABELS = {
    FULL_FINGERPRINT: {"en": "Full fingerprint", "fr": "Empreinte complète"},
    FULL_AGGREGATES: {"en": "Full aggregates", "fr": "Agrégats complets"},
    PARTITION_SAMPLED: {"en": "Sampled partitions", "fr": "Partitions échantillonnées"},
    ROW_SAMPLED: {"en": "Sampled rows", "fr": "Lignes échantillonnées"},
    METADATA_ONLY: {"en": "Metadata only", "fr": "Métadonnées seules"},
    NOT_COMPARED: {"en": "Not compared", "fr": "Non comparé"},
}

DEFINITIONS = {
    FULL_FINGERPRINT: {
        "en": "A content fingerprint computed in both engines from a canonical form of every row, then compared (not implemented yet).",
        "fr": "Une empreinte de contenu calculée dans les deux moteurs à partir d'une forme canonique de chaque ligne, puis comparée "
              "(pas encore disponible)."},
    FULL_AGGREGATES: {
        "en": "Row count and, per column, non-null count, minimum, maximum and sum recomputed in the source engine over the whole "
              "table and compared with the copy; plus a fingerprint of the rows as Canot read them, compared with the copy.",
        "fr": "Nombre de lignes et, par colonne, nombre de valeurs non nulles, minimum, maximum et somme recalculés dans le moteur "
              "source sur toute la table et comparés à la copie ; plus une empreinte des lignes telles que Canot les a lues, comparée "
              "à la copie."},
    PARTITION_SAMPLED: {
        "en": "Full aggregates everywhere, plus a full fingerprint in partitions drawn with a recorded seed (not implemented yet).",
        "fr": "Agrégats complets partout, plus une empreinte complète dans des partitions tirées avec une graine consignée "
              "(pas encore disponible)."},
    ROW_SAMPLED: {
        "en": "Rows selected by a hash of their key and compared value by value (not implemented yet).",
        "fr": "Des lignes choisies par hachage de leur clé et comparées valeur par valeur (pas encore disponible)."},
    METADATA_ONLY: {
        "en": "Only catalog metadata (structure, declared row count) compared: the content was not read.",
        "fr": "Seules les métadonnées du catalogue (structure, nombre de lignes déclaré) sont comparées : le contenu n'a pas été lu."},
    NOT_COMPARED: {
        "en": "Nothing compared (copy error, comparison error, or table not copied); the reason is given.",
        "fr": "Rien n'a été comparé (erreur de copie, erreur de comparaison, ou table non copiée) ; la raison est donnée."},
}

# proof_note of a full_aggregates comparison (English, like report.json); the HTML report words it per language from the data
NOTE_FULL_AGGREGATES = ("row count and per-column non-null count, min, max and sum (numeric columns) recomputed in the source engine "
                        "over the whole table and compared with the copy; content fingerprint of the rows as Canot read them compared "
                        "with the copy (it shows the copy matches what Canot read, not the source itself)")
NOTE_WITHIN_PRECISION = "; compared only within the precision of the target type: {cols}"


def rank(level: str) -> int:
    """Position on the scale (0 = weakest). An unknown identifier counts as not compared: never stronger than what is known."""
    return ORDER.index(level) if level in ORDER else 0


def lowest(levels: Iterable[str]) -> str:
    """The exercise level: the lowest of its tables. Nothing compared at all: not_compared."""
    levels = list(levels)
    return min(levels, key=rank) if levels else NOT_COMPARED


def counts(levels: Iterable[str]) -> dict[str, int]:
    """Number of tables (or models) per level, every level present, weakest first: stable keys for machines."""
    levels = list(levels)
    return {lv: sum(1 for x in levels if x == lv) for lv in ORDER}


def label(level: str, lang: str = "en") -> str:
    return LABELS.get(level, {}).get("fr" if lang == "fr" else "en", level)


def full_aggregates_note(within_precision: Iterable[str] = ()) -> str:
    cols = sorted(within_precision)
    return NOTE_FULL_AGGREGATES + (NOTE_WITHIN_PRECISION.format(cols=", ".join(cols)) if cols else "")

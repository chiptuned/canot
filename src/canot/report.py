"""The HTML evidence report of `canot drill`, in English and French (both in one file, switched without script), and report.json.

The body is built once per language from the same data (`_body(res, out, lang, ...)`) with the text table `T`; machine-written
strings of the run (query details, conversion notes, dbt reasons) are worded in French by `_tr` and `_cause_fr`, and stay as
written when no French wording is known (technical messages). report.json, like the command line, stays English."""
from __future__ import annotations

import os
import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path

from .target import local_path
from . import proof
from .regulatory import section as regulatory_section
from .brand import CSS as BRAND_CSS, bilingual, brand_bar, default_lang, drill_share, share_block

CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#14212b}
h1{font-size:1.7rem}h2{margin-top:2rem;border-bottom:1px solid #cdd7de;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e1e8ec;padding:6px 8px;text-align:left;vertical-align:top}
.ok{color:#1b7a3a;font-weight:600}.ko{color:#b3261e;font-weight:600}.warn{color:#9a5b00;font-weight:600}
code{background:#eef3f6;padding:1px 4px;border-radius:3px;word-break:break-all}.box{background:#eef3f6;padding:.8rem 1rem;border-radius:6px}
.verdict{font-size:1.6rem;line-height:1.2;margin:.4rem 0 1rem}.find{display:grid;grid-template-columns:110px 1fr;gap:16px;padding:14px 0;border-bottom:1px solid #e1e8ec}
.tag{align-self:start;font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;text-align:center}.tag.ok{background:#e7f2ea;color:#2f7a4b}.tag.warn{background:#fbf1ea;color:#c8652f}.tag.info{background:#e6ecf2;color:#1f3a5f}
.find b{display:block;font-size:1.05rem}.find span{color:#41566b}.cta{background:#1f3a5f;color:#fff;padding:1rem 1.2rem;border-radius:8px;margin-top:2rem}.cta a{color:#ffb88f;font-weight:600}
.brandbar .langbar{margin:0}
@media(max-width:560px){.find{grid-template-columns:1fr}}"""

LANGS = ("en", "fr")
CONTACT_URL = {"en": "https://canot.io/en/#contact", "fr": "https://canot.io/#contact"}
_MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")

# Every human-readable string of the report, per language. Values with {fields} are str.format templates.
T = {
    "en": {
        "title": "Canot evidence report",
        "h1": "Evidence report: exit exercise",
        "meta": "Source: {src} · Target: {dst} · Finding dated {date}, on the scope run · Canot {v}",
        "summary": "Summary",
        # synthese
        "tag_match": "Matches", "tag_mismatch": "Mismatch", "tag_of": "{m} of {n}", "tag_todo": "To address",
        "tag_skipped": "Not replayed", "tag_signed": "Signed",
        "f_match": "Counts and statistics match.", "f_match_prec": "Counts and statistics match, except for precision.",
        "f_match_d": ("All {n} tables, {rows} rows in total, were copied then compared (proof level: {lvl}). Row counts and per-column "
                      "statistics recomputed in the source engine match the copy, and the copy matches the rows as Canot read them "
                      "(content fingerprint). The content is not compared value by value with the source: see Proof level."),
        "f_match_d_prec": " Only the columns flagged below differ.", "f_match_d_none": " No difference observed.",
        "f_ko": "Some data differs.",
        "f_ko_d": "{k} table(s) out of {t} do not match or could not be compared: fix them before drawing conclusions.",
        "f_q_same": "Your reports give the same figures.", "f_q_almost": "Your reports give almost the same figures.",
        "f_q_d": "{n} reporting queries were replayed on both sides. {m} return exactly the same result.",
        "f_qm": "A report returns a different result.",
        "f_qm_d": "{sql}…: {detail}. The figures may be right and only the presentation differs: better to know before leaving.",
        "f_qm_default": "different result",
        "f_qu": "{n} query(ies) could not be replayed.",
        "f_qu_d": "Function or syntax specific to the source platform: rewrite it for the destination.",
        "f_qs": "{n} query(ies) read tables that are too large.",
        "f_qs_d": "The replay loads tables in memory. Raise --replay-max-gb or replay on a sample.",
        "f_prec": "{n} column(s) lose precision on arrival.",
        "f_prec_line": "{t}.{col}: {src} becomes {tgt}",
        "f_prec_d": ". The values differ beyond the precision of the target type. This is not corruption: it is a type choice to make before leaving.",
        "f_lossy": "{n} type conversion(s) with possible loss.",
        "f_lossy_d": "No difference observed on this data, but other values could show one: the list is below.",
        "f_objs": "{n} object(s) do not move as is.",
        "f_objs_d": "Views, routines, scheduled tasks: rebuild them on the destination.",
        "f_signed": "Any change to the log shows.",
        "f_signed_d": ("Each step is recorded in a log signed with a key created on your side. The signature attests the integrity of the "
                       "log, not the identity of its author nor the date: the key fingerprint is shown at the bottom of the report."),
        "v_ko": "Exit not proven on this scope: some data differs.",
        "v_none": "Exit feasible on this scope. Nothing to address before leaving.",
        "v_one": "Exit feasible on this scope. One point to address before leaving.",
        "v_many": "Exit feasible on this scope. {n} points to address before leaving.",
        # finding box
        "box": "Finding:", "box_ok": "counts and statistics match", "box_ko": "mismatches to review",
        "box_d": ("{tok}/{tt} matching tables (proof level: {lvl}), {qm}/{qt} queries with the same result, {qu} not replayable, "
                  "{qx} with a mismatch, {lc} conversion(s) with possible loss."),
        # measurements
        "meas": "Measurements from this exercise",
        "meas_tp": "Measured copy throughput: {v} MB/s",
        "meas_rto": "Estimated recovery time (range): {lo} to {hi} s — {basis}",
        "meas_cr": "Warehouse credits used (est.): {v}", "meas_cost": " · cost: {c} USD ({basis})",
        "meas_dur": "Total exercise duration: {v} s",
        "meas_note": "These measurements describe this exercise, on this scope. They do not compare two platforms.",
        "range": "{a} to {b}",
        # copy location
        "where": "Where the copy is",
        "where_d": "Destination: {dst} · location: {loc} · catalog: {cat}.",
        "where_iceberg": ("The copy is in Apache Iceberg format: you can reread it without Canot. The {f} file next to this report "
                          "holds the DuckDB commands to do so."),
        # tables
        "tables": "Tables", "th_tables": ("Table", "Source rows", "Target rows", "Result", "Proof level", "Details"),
        "r_match": "matches", "r_precision": "precision", "r_mismatch": "mismatch", "r_not_compared": "not compared", "buckets": " buckets ",
        "within_prec": "compared only within the precision of the target type: {cols}",
        # proof level
        "proof": "Proof level",
        "proof_reached": "Level reached by this exercise: {lvl} ({code}), the lowest level over its tables ({counts}).",
        "proof_count_t": "{n} table(s): {lvl}", "proof_count_m": "{n} model(s): {lvl}",
        "proof_today": " This is what Canot does today.",
        "proof_sampled": "{a} and {b}: sampled levels (partitions drawn with a recorded seed, or rows selected by key), reserved; not implemented yet.",
        "proof_low": ("{a} and {b}: only catalog metadata compared, or nothing compared (copy error, comparison error, table not copied), "
                      "with the reason."),
        "proof_not": ("What full aggregates do not prove: a value changed inside a text column that keeps the same count, minimum and "
                      "maximum would not be detected by the recomputation in the source engine, and the content fingerprint only shows "
                      "that the copy matches what Canot read, not that Canot read the source faithfully. A stronger check, a content "
                      "fingerprint computed in both engines, is not implemented yet."),
        # queries
        "queries": "Queries replayed on both sides", "th_queries": ("Query", "Result", "Details"),
        "q_status": {"match": "match", "mismatch": "mismatch", "untranslatable": "untranslatable", "error": "error", "skipped": "not replayed"},
        "q_order": " (different order)", "none_f": "none", "none_m": "none",
        # conversions, what does not move
        "convs": "Type conversions", "possible_loss": " (possible loss)",
        "nomove": "What does not move as is",
        "nomove_d": "Columns with a platform-specific type: {n}. Objects to rebuild (views, procedures, tasks…):",
        "th_objs": ("Type", "Count", "Examples"), "none_detected": "none detected",
        "objs": {},
        # limits
        "limits": "What these figures do not say",
        "limits_l": ("They cover the scope that was run, on the date of the finding. They say nothing about other tables or about tomorrow.",
                     "They do not compare two platforms: no durations, costs or performance.",
                     "They do not cover access rights, views, scheduled tasks or the applications connected to the platform.",
                     "This is not a certification and not a legal opinion on your contracts."),
        "tol": "Tolerances applied",
        # integrity
        "integ": "Report integrity",
        "integ_log": "Chained log: {n} entries, last hash {h}. Any change to the log shows. Verification: {cmd}",
        "integ_key": "Fingerprint of the public signing key: {fp}.",
        "integ_sig": ("The signature attests the integrity of the log. It establishes neither the identity of the author nor the date: "
                      "for that, keep the report in your own timestamped archive."),
        "key_na": "not available",
        "disclaimer": ("This report is a record of measurements for your own assessment. It is not a certification and it does not say "
                       "whether your contracts or your organization meet any regulation."),
        "cta": ("This report covers the scope you chose. To get it on your environment, with three exit-cost scenarios and the steps to "
                "follow:"),
        # dbt section
        "dbt": "dbt project",
        "dbt_head": "Project {p}, profile {pr}, target {c}, adapter {a}. {lect}: nothing was written to the warehouse.",
        "dbt_rel": "Relations copied: {n} (sources, seeds, snapshots, table and incremental models). Row access and masking policies: {pol}.",
        "dbt_pol_none": "none on the copied tables", "dbt_pol_unread": "not read",
        "dbt_rejeu": ("Replay of the models on the copy (P3): {i} matching, {d} different, {nc} replayed but not compared (no table to "
                      "compare: views), {nr} not replayable, out of {m}. Details per model: {f}."),
        "dbt_proof": ("Proof level of the replay: {lvl} ({code}), the lowest over the models ({counts}). A model replay compares the "
                      "counts, statistics and fingerprint of the replayed output with the original table."),
        "dbt_tz": "Time zone of the replay: {z} ({rule}",
        "th_dbt_ex": ("Model", "Difference", "Explanation"), "dbt_not": "Not copied", "th_dbt_nc": ("Relation", "Kind", "Why"),
        "also": " Also: ", "sep": "; ", "colon": ": ",
    },
    "fr": {
        "title": "Rapport de preuve Canot",
        "h1": "Rapport de preuve : exercice de sortie",
        "meta": "Source : {src} · Destination : {dst} · Constat daté du {date}, sur le périmètre exécuté · Canot {v}",
        "summary": "Synthèse",
        "tag_match": "Concordant", "tag_mismatch": "Écart", "tag_of": "{m} sur {n}", "tag_todo": "À traiter",
        "tag_skipped": "Non rejoué", "tag_signed": "Signé",
        "f_match": "Les comptes et statistiques concordent.", "f_match_prec": "Les comptes et statistiques concordent, à la précision près.",
        "f_match_d": ("Les {n} tables, {rows} lignes au total, ont été copiées puis comparées (niveau de preuve : {lvl}). Le nombre de "
                      "lignes et les statistiques par colonne recalculés dans le moteur source concordent avec la copie, et la copie "
                      "concorde avec les lignes telles que Canot les a lues (empreinte de contenu). Le contenu n'est pas comparé valeur "
                      "par valeur avec la source : voir Niveau de preuve."),
        "f_match_d_prec": " Seules les colonnes signalées ci-dessous diffèrent.", "f_match_d_none": " Aucun écart observé.",
        "f_ko": "Certaines données diffèrent.",
        "f_ko_d": "{k} table(s) sur {t} ne concordent pas ou n'ont pas pu être comparées : corrigez-les avant de conclure.",
        "f_q_same": "Vos rapports donnent les mêmes chiffres.", "f_q_almost": "Vos rapports donnent presque les mêmes chiffres.",
        "f_q_d": "{n} requêtes de reporting ont été rejouées des deux côtés. {m} renvoient exactement le même résultat.",
        "f_qm": "Un rapport renvoie un résultat différent.",
        "f_qm_d": ("{sql}… : {detail}. Les chiffres sont peut-être justes et seule la présentation diffère : mieux vaut le savoir "
                   "avant de partir."),
        "f_qm_default": "résultat différent",
        "f_qu": "{n} requête(s) n'ont pas pu être rejouées.",
        "f_qu_d": "Fonction ou syntaxe propre à la plateforme source : à réécrire pour la destination.",
        "f_qs": "{n} requête(s) lisent des tables trop volumineuses.",
        "f_qs_d": "Le rejeu charge les tables en mémoire. Augmentez --replay-max-gb ou rejouez sur un échantillon.",
        "f_prec": "{n} colonne(s) perdent en précision à l'arrivée.",
        "f_prec_line": "{t}.{col} : {src} devient {tgt}",
        "f_prec_d": (". Les valeurs diffèrent au-delà de la précision du type cible. Ce n'est pas une corruption : c'est un choix de "
                     "type à faire avant de partir."),
        "f_lossy": "{n} conversion(s) de type avec perte possible.",
        "f_lossy_d": "Aucune différence observée sur ces données, mais d'autres valeurs pourraient en montrer une : la liste figure plus bas.",
        "f_objs": "{n} objet(s) ne se déplacent pas tels quels.",
        "f_objs_d": "Vues, routines, tâches planifiées : à reconstruire sur la destination.",
        "f_signed": "Toute modification du journal se voit.",
        "f_signed_d": ("Chaque étape est consignée dans un journal signé avec une clé créée de votre côté. La signature atteste "
                       "l'intégrité du journal, ni l'identité de son auteur ni la date : l'empreinte de la clé figure en bas du rapport."),
        "v_ko": "Sortie non démontrée sur ce périmètre : certaines données diffèrent.",
        "v_none": "Sortie réalisable sur ce périmètre. Rien à traiter avant de partir.",
        "v_one": "Sortie réalisable sur ce périmètre. Un point à traiter avant de partir.",
        "v_many": "Sortie réalisable sur ce périmètre. {n} points à traiter avant de partir.",
        "box": "Constat :", "box_ok": "comptes et statistiques concordants", "box_ko": "écarts à examiner",
        "box_d": ("{tok}/{tt} tables concordantes (niveau de preuve : {lvl}), {qm}/{qt} requêtes au même résultat, {qu} non "
                  "rejouable(s), {qx} avec un écart, {lc} conversion(s) avec perte possible."),
        "meas": "Mesures de cet exercice",
        "meas_tp": "Débit de copie mesuré : {v} Mo/s",
        "meas_rto": "Temps de reprise estimé (fourchette) : {lo} à {hi} s — {basis}",
        "meas_cr": "Crédits d'entrepôt consommés (est.) : {v}", "meas_cost": " · coût : {c} USD ({basis})",
        "meas_dur": "Durée totale de l'exercice : {v} s",
        "meas_note": "Ces mesures décrivent cet exercice, sur ce périmètre. Elles ne comparent pas deux plateformes.",
        "range": "{a} à {b}",
        "where": "Où se trouve la copie",
        "where_d": "Destination : {dst} · emplacement : {loc} · catalogue : {cat}.",
        "where_iceberg": ("La copie est au format Apache Iceberg : vous pouvez la relire sans Canot. Le fichier {f}, à côté de ce "
                          "rapport, contient les commandes DuckDB pour le faire."),
        "tables": "Tables", "th_tables": ("Table", "Lignes source", "Lignes cible", "Résultat", "Niveau de preuve", "Détails"),
        "r_match": "concordante", "r_precision": "précision", "r_mismatch": "écart", "r_not_compared": "non comparée",
        "buckets": " compartiments d'empreinte ",
        "within_prec": "comparée seulement à la précision du type cible : {cols}",
        "proof": "Niveau de preuve",
        "proof_reached": "Niveau atteint par cet exercice : {lvl} ({code}), le plus bas de ses tables ({counts}).",
        "proof_count_t": "{n} table(s) : {lvl}", "proof_count_m": "{n} modèle(s) : {lvl}",
        "proof_today": " C'est ce que fait Canot aujourd'hui.",
        "proof_sampled": ("{a} et {b} : niveaux par échantillon (partitions tirées avec une graine consignée, ou lignes choisies par clé), "
                          "réservés ; pas encore disponibles."),
        "proof_low": ("{a} et {b} : seules les métadonnées du catalogue comparées, ou rien de comparé (erreur de copie, erreur de "
                      "comparaison, table non copiée), avec la raison."),
        "proof_not": ("Ce que les agrégats complets ne prouvent pas : une valeur modifiée à l'intérieur d'une colonne de texte qui garde "
                      "le même nombre de valeurs, le même minimum et le même maximum ne serait pas détectée par le recalcul dans le "
                      "moteur source, et l'empreinte de contenu montre seulement que la copie correspond à ce que Canot a lu, pas que "
                      "Canot a lu fidèlement la source. Un contrôle plus fort, une empreinte de contenu calculée dans les deux moteurs, "
                      "n'est pas encore disponible."),
        "queries": "Requêtes rejouées des deux côtés", "th_queries": ("Requête", "Résultat", "Détails"),
        "q_status": {"match": "même résultat", "mismatch": "écart", "untranslatable": "non traduisible", "error": "erreur",
                     "skipped": "non rejouée"},
        "q_order": " (ordre différent)", "none_f": "aucune", "none_m": "aucun",
        "convs": "Conversions de type", "possible_loss": " (perte possible)",
        "nomove": "Ce qui ne se déplace pas tel quel",
        "nomove_d": "Colonnes d'un type propre à la plateforme : {n}. Objets à reconstruire (vues, procédures, tâches…) :",
        "th_objs": ("Type", "Nombre", "Exemples"), "none_detected": "aucun détecté",
        "objs": {"views": "vues", "materialized_views": "vues matérialisées", "procedures": "procédures", "functions": "fonctions",
                 "routines": "routines", "tasks": "tâches", "streams": "streams", "pipes": "pipes", "dynamic_tables": "tables dynamiques",
                 "external_tables": "tables externes", "row_access_policies": "politiques d'accès aux lignes", "ml_models": "modèles ML",
                 "macros": "macros"},
        "limits": "Ce que ces chiffres ne disent pas",
        "limits_l": ("Ils couvrent le périmètre exécuté, à la date du constat. Ils ne disent rien des autres tables ni de demain.",
                     "Ils ne comparent pas deux plateformes : ni durées, ni coûts, ni performances.",
                     "Ils ne couvrent pas les droits d'accès, les vues, les tâches planifiées ni les applications connectées à la plateforme.",
                     "Ce n'est ni une certification ni un avis juridique sur vos contrats."),
        "tol": "Tolérances appliquées",
        "integ": "Intégrité du rapport",
        "integ_log": "Journal chaîné : {n} entrées, dernier hachage {h}. Toute modification du journal se voit. Vérification : {cmd}",
        "integ_key": "Empreinte de la clé publique de signature : {fp}.",
        "integ_sig": ("La signature atteste l'intégrité du journal. Elle n'établit ni l'identité de l'auteur ni la date : pour cela, "
                      "conservez le rapport dans votre propre archive horodatée."),
        "key_na": "non disponible",
        "disclaimer": ("Ce rapport consigne des mesures pour votre propre évaluation. Ce n'est pas une certification, et il ne dit pas "
                       "si vos contrats ou votre organisation satisfont à une réglementation."),
        "cta": ("Ce rapport couvre le périmètre que vous avez choisi. Pour l'obtenir sur votre environnement, avec trois scénarios de "
                "coût de sortie et les étapes à suivre :"),
        "dbt": "Projet dbt",
        "dbt_head": "Projet {p}, profil {pr}, cible {c}, adaptateur {a}. {lect} : rien n'a été écrit dans l'entrepôt.",
        "dbt_rel": ("Relations copiées : {n} (sources, seeds, snapshots, modèles table et incremental). Politiques d'accès aux lignes "
                    "et de masquage : {pol}."),
        "dbt_pol_none": "aucune sur les tables copiées", "dbt_pol_unread": "non lues",
        "dbt_rejeu": ("Rejeu des modèles sur la copie (P3) : {i} concordant(s), {d} différent(s), {nc} rejoué(s) sans comparaison (pas "
                      "de table à comparer : vues), {nr} non rejouable(s), sur {m}. Détail par modèle : {f}."),
        "dbt_proof": ("Niveau de preuve du rejeu : {lvl} ({code}), le plus bas des modèles ({counts}). Le rejeu d'un modèle compare les "
                      "comptes, les statistiques et l'empreinte de la sortie rejouée avec la table d'origine."),
        "dbt_tz": "Fuseau horaire du rejeu : {z} ({rule}",
        "th_dbt_ex": ("Modèle", "Écart", "Explication"), "dbt_not": "Non copiées", "th_dbt_nc": ("Relation", "Nature", "Raison"),
        "also": " Par ailleurs : ", "sep": " ; ", "colon": " : ",
    },
}

# French wording of the strings the run writes (verify, convert, drill, prices, dbt): (pattern, replacement), full match.
_R_GENERIQUE_FR = ("causes possibles, à vérifier dans replay-dbt.json : une entrée lue à travers une politique d'accès aux lignes ou de "
                   "masquage qui filtre ce que voit le rôle du profil (la copie est alors partielle pour ce lecteur) ; une table de la "
                   "plateforme construite à partir d'entrées plus anciennes (dernier dbt run antérieur au dernier chargement) ; une vraie "
                   "différence entre les deux moteurs.")
_PLUSIEURS_FR = ("plusieurs fuseaux sources : les conversions en date peuvent différer, résultat non déterminé pour les modèles concernés "
                 "(rejoué en UTC)")
_FR = [(re.compile(a, re.S), b) for a, b in (
    # query replay (verify.py)
    (r"(\d+) rows / (\d+)", r"\1 lignes / \2"),
    (r"(\d+) columns / (\d+)", r"\1 colonnes / \2"),
    (r"sorted row (\d+), column (\d+): (.*)", r"ligne triée \1, colonne \2 : \3"),
    (r"table unknown to the copy: (.*)", r"table inconnue de la copie : \1"),
    (r"(\S+) weighs ([\d.]+) GB: too large for in-memory replay \(limit (\d+) GB, see --replay-max-gb\)",
     r"\1 pèse \2 Go : trop volumineuse pour le rejeu en mémoire (limite \3 Go, voir --replay-max-gb)"),
    (r"row count: ([^;]*)", r"nombre de lignes : \1"),
    (r"copy failed: (.*)", r"échec de la copie : \1"),
    (r"comparison failed: (.*)", r"échec de la comparaison : \1"),
    (r"not compared: (.*)", r"non comparé : \1"),
    (r"only the column lists were compared \(they differ\): content not compared",
     "seules les listes de colonnes ont été comparées (elles diffèrent) : contenu non comparé"),
    # conversions (convert.py)
    (r"time zone (\S+) -> UTC \(Iceberg keeps UTC only\): the instant is kept, the zone name is not",
     r"fuseau \1 -> UTC (Iceberg ne garde que l'UTC) : l'instant est conservé, pas le nom du fuseau"),
    (r"ns -> us \(Iceberg v2 does not keep nanoseconds\)", "ns -> us (Iceberg v2 ne conserve pas les nanosecondes)"),
    (r"all-null column, typed as text", "colonne entièrement nulle, typée en texte"),
    (r"unsigned integer -> signed", "entier non signé -> signé"),
    (r"precision > 38 stored as text", "précision > 38 stockée en texte"),
    (r"native Snowflake TIMESTAMP_TZ: original time zone kept as text", "TIMESTAMP_TZ natif Snowflake : fuseau d'origine conservé en texte"),
    (r"normalized type", "type normalisé"),
    (r"SQLite \(catalog\.db in the output folder\)", "SQLite (catalog.db dans le dossier de sortie)"),
    (r"custom", "personnalisé"),
    # measurements (drill.py, prices.py)
    (r"measured throughput ±, excluding human validation and application cutover",
     "débit mesuré ±, hors validation humaine et bascule des applications"),
    (r"credit price provided: (.*) USD", r"prix du crédit fourni : \1 USD"),
    (r"Snowflake list price as of (\S+), AWS Paris/Frankfurt, from lowest \((\S+) USD, Standard\) to highest \((\S+) USD, (.+?)\): (\S+)",
     r"tarif catalogue Snowflake au \1, AWS Paris/Francfort, du plus bas (\2 USD, Standard) au plus haut (\3 USD, \4) : \5"),
    # dbt (dbt_drill.py, dbt_rejeu.py)
    (r"read-only, with (.*)", r"lecture seule, avec \1"),
    (r"role (\S+)", r"le rôle \1"),
    (r"the user's default role", "le rôle par défaut de l'utilisateur"),
    (r"the profile's role", "le rôle du profil"),
    (r"the Google credentials of the profile", "les identifiants Google du profil"),
    (r"your Google application default credentials", "vos identifiants Google par défaut (application default credentials)"),
    (r"the profile's token \(DATABRICKS_TOKEN\)", "le jeton du profil (DATABRICKS_TOKEN)"),
    (r"the local DuckDB file, read-only", "le fichier DuckDB local, en lecture seule"),
    (r"view: holds no data; not copied, proven through the tables that read it \(replayed, compared through its descendants\)",
     "vue : ne contient pas de données ; non copiée, démontrée par les tables qui la lisent (rejouées, comparées à travers ses descendants)"),
    (r"ephemeral: no relation in the warehouse \(inlined in the models that use it\)",
     "ephemeral : aucune relation dans l'entrepôt (intégré aux modèles qui l'utilisent)"),
    (r"not found as a table in the warehouse \(not built yet, an external table, or not visible to the profile's role\)",
     "introuvable comme table dans l'entrepôt (pas encore construite, table externe, ou invisible pour le rôle du profil)"),
    (r"no relation name in the manifest", "aucun nom de relation dans le manifest"),
    (r"materialization (\S+): maintained by the platform, not a plain table; not copied \(to rebuild on the destination\), the tables it reads are",
     r"matérialisation \1 : maintenue par la plateforme, pas une simple table ; non copiée (à reconstruire sur la destination), les tables qu'elle lit le sont"),
    (r"in database (\S+); the profile connects to (\S+): run canot drill on that database",
     r"dans la base \1 ; le profil se connecte à \2 : lancez canot drill sur cette base"),
    (r"left out by --tables", "écartée par --tables"),
    (r"not read: policies are read on Snowflake only", "non lues : les politiques ne sont lues que sur Snowflake"),
    (r"not readable with the profile's role \((.*)\)", r"illisibles avec le rôle du profil (\1)"),
    (r"different content fingerprint", "empreinte de contenu différente"),
    (r"conversion to the original types failed \((.*)\)", r"échec de la conversion vers les types d'origine (\1)"),
    (r"source time zone of the inputs, applied to the replay", "fuseau source des entrées, appliqué au rejeu"),
    (r"no source time zone among the inputs: UTC, explicitly", "aucun fuseau source parmi les entrées : UTC, explicitement"),
    (r"source time zone not applicable: UTC, explicitly", "fuseau source non applicable : UTC, explicitement"),
    (r"several source time zones: conversions to a date can differ, result not determined for the models concerned \(replayed in UTC\)(.*)",
     _PLUSIEURS_FR.replace("\\", "\\\\") + r"\1"),
    (r"source time zone (\S+) unknown to DuckDB: replayed in UTC; conversions to a date can differ",
     r"fuseau source \1 inconnu de DuckDB : rejoué en UTC ; les conversions en date peuvent différer"),
)]


def _e(x) -> str:
    return html.escape(str(x))


def _h(tpl: str, **fields) -> str:
    """A text template of T filled with fields that are already HTML (code elements, escaped values)."""
    return html.escape(tpl, quote=False).format(**fields)


def _tr(s, lang: str):
    """A string written by the run, in the pane's language. French: the known wordings; "a; b" is worded part by part; an unknown
    string (a technical message, an identifier) stays as written."""
    if lang != "fr" or not isinstance(s, str) or not s:
        return s
    for rx, rep in _FR:
        m = rx.fullmatch(s)
        if m:
            out = m.expand(rep)
            return out.replace(": ", " : ") if rx.pattern.startswith("several") else out
    if "; " in s:
        parts = s.split("; ")
        done = [_tr(p, lang) for p in parts]
        if done != parts:
            return " ; ".join(done)
    return s


def _n(x, lang: str, group: bool = False) -> str:
    """A number in the pane's language. English: as before (1234, or 1,234 with group). French: 1 234,5 (narrow no-break space)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if lang == "fr":
        return f"{x:,}".replace(",", " ").replace(".", ",")
    return f"{x:,}" if group else str(x)


def _date(d: datetime, lang: str) -> str:
    if lang == "fr":
        return f"{'1er' if d.day == 1 else d.day} {_MOIS[d.month - 1]} {d.year}, {d:%H:%M}"
    return d.strftime("%Y-%m-%d %H:%M")


def proof_levels(res: dict) -> tuple[str, dict[str, int]]:
    """(level of the exercise, tables per level). A report.json written before the levels existed: same computation as today,
    so its compared tables are full_aggregates and its tables in error not_compared."""
    s = res["summary"]
    if s.get("proof_level"):
        return s["proof_level"], s.get("tables_by_proof_level") or {}
    levels = [c.get("proof_level") or proof.FULL_AGGREGATES for c in res["checks"]] + [proof.NOT_COMPARED for _ in s.get("tables_error", [])]
    return proof.lowest(levels), proof.counts(levels)


def _counts_text(counts: dict[str, int], lang: str, tpl: str) -> str:
    t = T[lang]
    return t["sep"].join(t[tpl].format(n=_n(n, lang), lvl=proof.label(lv, lang)) for lv, n in counts.items() if n)


def _proof_section(res: dict, lang: str) -> str:
    """Fixed section: the level reached, the scale in four lines, what full_aggregates does not prove."""
    t = T[lang]
    lvl, counts = proof_levels(res)
    code = lambda x: f"<code>{_e(x)}</code>"  # noqa: E731
    d = lambda x: _e(proof.DEFINITIONS[x][lang])  # noqa: E731
    lines = [f"{code(proof.FULL_FINGERPRINT)} ({_e(proof.label(proof.FULL_FINGERPRINT, lang))}){_e(t['colon'])}{d(proof.FULL_FINGERPRINT)}",
             f"{code(proof.FULL_AGGREGATES)} ({_e(proof.label(proof.FULL_AGGREGATES, lang))}){_e(t['colon'])}{d(proof.FULL_AGGREGATES)}{_e(t['proof_today'])}",
             _h(t["proof_sampled"], a=code(proof.PARTITION_SAMPLED), b=code(proof.ROW_SAMPLED)),
             _h(t["proof_low"], a=code(proof.METADATA_ONLY), b=code(proof.NOT_COMPARED))]
    return (f"<h2>{_e(t['proof'])}</h2><p>"
            + _h(t["proof_reached"], lvl=f"<b>{_e(proof.label(lvl, lang))}</b>", code=code(lvl), counts=_e(_counts_text(counts, lang, "proof_count_t")))
            + "</p><ul>" + "".join(f"<li>{x}</li>" for x in lines) + f"</ul><p>{_e(t['proof_not'])}</p>")


def synthese(res: dict, lang: str = "en") -> dict:
    """The finding in plain language: what matches (at the proof level stated), what differs, what needs action (English by default, or French)."""
    t = T["fr" if lang == "fr" else "en"]
    s = res["summary"]
    tabs = res["checks"]
    rows = sum(c["rows_target"] for c in tabs)
    prec = [c for c in tabs if not c["ok"] and c.get("precision_cols") and c["rows_source"] == c["rows_target"] and c["fingerprint_ok"] and c["schema_ok"]
            and all(d.split(".")[0] in c["precision_cols"] for d in c["stats_diffs"])]
    ko = [c for c in tabs if not c["ok"] and c not in prec]
    qm = [q for q in res["queries"] if q["status"] == "mismatch"]
    qu = [q for q in res["queries"] if q["status"] in ("untranslatable", "error")]
    qs = [q for q in res["queries"] if q["status"] == "skipped"]
    objs = sum(len(v) for v in res["scan"]["objects_that_do_not_move"].values())
    conv = {(c["table"], c["column"]): c for c in res["conversions"]}
    prec_cols = {(c["table"], col) for c in prec for col in c["precision_cols"]}
    lossy_no_diff = [c for c in res["conversions"] if c["lossy"] and (c["table"], c["column"]) not in prec_cols]
    todo = len(qm) + len(qu) + (1 if prec_cols else 0) + (1 if lossy_no_diff else 0) + (1 if objs else 0) + len(s["tables_error"])
    n = lambda x: _n(x, lang)  # noqa: E731
    f = []
    if not ko and not s["tables_error"]:
        lvl = proof.label(proof_levels(res)[0], lang).lower()
        f.append(("ok", t["tag_match"], t["f_match"] if not prec else t["f_match_prec"],
                  t["f_match_d"].format(n=n(len(tabs)), rows=_n(rows, lang, True), lvl=lvl) + (t["f_match_d_prec"] if prec else t["f_match_d_none"])))
    else:
        f.append(("warn", t["tag_mismatch"], t["f_ko"], t["f_ko_d"].format(k=n(len(ko) + len(s["tables_error"])), t=n(s["tables_total"]))))
    if res["queries"]:
        done = [q for q in res["queries"] if q["status"] != "skipped"]
        tag = "ok" if not qm and not qu else "info"
        f.append((tag, t["tag_of"].format(m=n(s["queries_match"]), n=n(len(done))), t["f_q_same"] if tag == "ok" else t["f_q_almost"],
                  t["f_q_d"].format(n=n(len(done)), m=n(s["queries_match"]))))
    for q in qm:
        f.append(("warn", t["tag_todo"], t["f_qm"], t["f_qm_d"].format(sql=q["sql"][:110].strip(), detail=_tr(q["detail"], lang) or t["f_qm_default"])))
    if qu:
        f.append(("warn", t["tag_todo"], t["f_qu"].format(n=n(len(qu))), t["f_qu_d"]))
    if qs:
        f.append(("info", t["tag_skipped"], t["f_qs"].format(n=n(len(qs))), t["f_qs_d"]))
    if prec_cols:
        lines = []
        for tb, col in sorted(prec_cols):
            c = conv.get((tb, col))
            lines.append(t["f_prec_line"].format(t=tb, col=col, src=c["source_type"], tgt=c["target_type"]) if c else f"{tb}.{col}")
        f.append(("warn", t["tag_todo"], t["f_prec"].format(n=n(len(prec_cols))), " · ".join(lines) + t["f_prec_d"]))
    if lossy_no_diff:
        f.append(("warn", t["tag_todo"], t["f_lossy"].format(n=n(len(lossy_no_diff))), t["f_lossy_d"]))
    if objs:
        f.append(("warn", t["tag_todo"], t["f_objs"].format(n=n(objs)), t["f_objs_d"]))
    f.append(("info", t["tag_signed"], t["f_signed"], t["f_signed_d"]))
    if ko or s["tables_error"]:
        verdict = t["v_ko"]
    elif todo == 0:
        verdict = t["v_none"]
    else:
        verdict = t["v_one"] if todo == 1 else t["v_many"].format(n=n(todo))
    return {"verdict": verdict, "findings": f, "todo": todo}


def _key_fingerprint(out: Path) -> str | None:
    try:
        from cryptography.hazmat.primitives import serialization

        pub = serialization.load_pem_public_key((out / "evidence.pub").read_bytes())
        raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        h = hashlib.sha256(raw).hexdigest()
        return " ".join(h[i:i + 8] for i in range(0, 32, 8))
    except Exception:
        return None


REREAD_FILE = "reread.sql"


def write_relire_sql(res: dict, out: Path) -> Path:
    """A SQL file to reread the copy with DuckDB, without Canot: evidence that it does not depend on the tool."""
    loc = res.get("copy_location") or {}
    lines = ["-- Reread the copy without Canot, with DuckDB (https://duckdb.org).", f"-- Run: duckdb < {REREAD_FILE}", "INSTALL iceberg; LOAD iceberg;"]
    if loc.get("kind") == "s3":
        lines += ["-- S3-compatible storage: first create a secret with YOUR keys (never paste them into a shared file).",
                  "-- CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...', ENDPOINT 's3-host', URL_STYLE 'path');"]
    for fqn, meta in sorted((loc.get("metadata") or {}).items()):
        path = local_path(meta).as_posix() if meta.startswith("file://") else meta.replace("\\", "/")  # DuckDB reads C:/... on Windows
        if os.name == "nt" and len(path) > 1 and path[1] == ":":
            # The Iceberg metadata written on Windows stores file:// paths that DuckDB's iceberg reader cannot open:
            # count the rows in the Parquet data files of the table, which every engine reads.
            data = path.rsplit("/metadata/", 1)[0] + "/data/**/*.parquet"
            lines.append(f"SELECT '{fqn}' AS table_name, count(*) AS row_count FROM read_parquet('{data}');")
        else:
            lines.append(f"SELECT '{fqn}' AS table_name, count(*) AS row_count FROM iceberg_scan('{path}');")
    p = out / REREAD_FILE
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return p


def _fmt_cost(c, lang: str = "en") -> str:
    if isinstance(c, (list, tuple)):
        return _n(c[0], lang) if c[0] == c[1] else T[lang]["range"].format(a=_n(c[0], lang), b=_n(c[1], lang))
    return _n(c, lang)


def _clean_path(p: str) -> str:
    """A local path becomes "…/folder": no user name in a report that gets shared."""
    return re.sub(r"^file://(?:/[^/ ]+)*/([^/ ]+/[^/ ]+)$", r"…/\1", str(p))


def _clean(label: str) -> str:
    """No local paths in a report that gets shared."""
    return re.sub(r"(local|duckdb):[^ ]*/([^/ ]+)", r"\1:\2", str(label))


def _policy_fr(items: list[dict]) -> str:
    kinds = {"row access": "accès aux lignes", "masking": "masquage"}

    def un(x):
        k = str(x.get("kind", "")).lower().replace("_", " ").replace(" policy", "")
        return f"politique {kinds.get(k, k)} {x.get('policy')}" + (f" sur {x['column']}" if x.get("column") else "")
    return "; ".join(un(x) for x in items)


def _cause_fr(c: dict) -> str:
    """The French text of one cause of a dbt difference, from its values (dbt_drill.explications, "p"). Without them (a report.json
    written before): the English text."""
    p = c.get("p")
    if not isinstance(p, dict):
        return c.get("texte") or ""
    nat = c.get("nature")
    if nat == "decalages":
        return (f"{p['ref']} a été copiée en UTC et le rejeu s'est fait dans son fuseau source {p['zone']}, mais les valeurs de date diffèrent "
                "encore : les décalages stockés avec les valeurs ne suivent pas les règles de ce fuseau (un TIMESTAMP_TZ conserve le décalage "
                "de chaque valeur : un décalage fixe malgré l'heure d'été, ou des décalages qui varient d'une ligne à l'autre). La copie ne "
                "garde que l'instant ; la règle de rejeu ne peut pas retrouver des décalages propres à chaque valeur. S'ils comptent, "
                "copiez-les dans une colonne séparée avant de partir (docs/scan.md).")
    if nat == "fuseau":
        ailleurs = p.get("applied") or "un autre fuseau"
        return ((f"{_tr(p['finding'], 'fr')}. " if p.get("finding") else "")
                + f"{p['ref']} est copiée comme un instant en UTC (fuseau source {p['zone']}) ; le rejeu s'est fait en {ailleurs} : une date ou "
                "une heure qui en dérive (cast en date, date_trunc, extract) peut différer de celle de la plateforme"
                + (" : les écarts sur les valeurs de date sont ici ce décalage" if p.get("dates") else "") + " (docs/discrepancies.md).")
    if nat == "conversion":
        return f"{p['ref']} est copiée avec une perte possible ({_tr(p['note'], 'fr')}) : un modèle calculé à partir d'elle peut différer."
    if nat == "politique":
        lecteur = _tr(p.get("reader") or "the profile's role", "fr")
        return ("lit " + ", ".join(f"{e} ({_policy_fr(x)})" for e, x in (p.get("policies") or {}).items())
                + f", et la sortie rejouée compte {_n(p['rows_target'], 'fr')} lignes là où la table de la plateforme en compte "
                f"{_n(p['rows_source'], 'fr')} : la copie contient ce que {lecteur} peut lire, alors que dbt a construit la table avec un rôle "
                "qui voyait plus de lignes ; le rejeu sur la copie ne peut pas la reproduire, et c'est le résultat attendu pour ce lecteur, "
                "pas une erreur de traduction (constat F10 de canot scan : une copie complète demande un principal que ces politiques ne "
                "filtrent pas).")
    if nat == "inconnue":
        nl = p.get("policies_not_read")
        return (f"politiques d'accès aux lignes et de masquage {_tr(nl, 'fr')} ; " if nl else "") + _R_GENERIQUE_FR
    return c.get("texte") or ""


def _dbt_section(res: dict, lang: str = "en") -> str:
    """canot drill dbt://: what was copied, what was not and why, the replay of the models, the explanation of each difference."""
    d = res.get("dbt")
    if not d:
        return ""
    t = T[lang]
    rj = d["rejeu"]
    rj_proof = ("<p>" + _h(t["dbt_proof"], lvl=f"<b>{_e(proof.label(rj['proof_level'], lang))}</b>", code=f"<code>{_e(rj['proof_level'])}</code>",
                           counts=_e(_counts_text(rj.get("models_by_proof_level") or {}, lang, "proof_count_m"))) + "</p>"
                if rj.get("proof_level") else "")  # a report.json written before the levels: no line
    th = lambda cols: "<tr>" + "".join(f"<th>{_e(c)}</th>" for c in cols) + "</tr>"  # noqa: E731
    nc = "".join(f"<tr><td>{_e(x.get('relation') or x['uid'])}</td><td>{_e(x.get('materialisation') or x['nature'])}</td><td>{_e(_tr(x['raison'], lang))}</td></tr>"
                 for x in d["non_copiees"])

    def expl(x):
        if lang != "fr":
            return x["explication"]
        return t["also"].join(_cause_fr(c) for c in x.get("causes") or []) or x["explication"]
    ex = "".join(f"<tr><td>{_e(x['modele'])}</td><td>{_e(_tr(x.get('raison') or '', lang))}</td><td>{_e(expl(x))}</td></tr>" for x in d["explications"])
    pol = d.get("politiques") or {}
    poltxt = ("; ".join(f"{tb}: " + ", ".join(f"{i['kind']} {i['policy']}" for i in v) for tb, v in (pol.get("tables") or {}).items())
              or (t["dbt_pol_none"] if pol.get("lu") else (_tr(pol.get("raison"), lang) or t["dbt_pol_unread"])))
    lect = _tr(d["lecture"], lang)
    if lang == "fr":
        m = re.fullmatch(r"lecture seule, avec (.*)", lect or "")
        lect = f"Lecture seule, avec {_tr(m.group(1), lang)}" if m else lect
    tz = d.get("fuseau") or {}
    tzp = (_h(t["dbt_tz"], z=f"<b>{_e(tz.get('applied'))}</b>", rule=_e(_tr(tz.get("rule"), lang)))
           + (t["colon"] + _e(", ".join(tz.get("origin") or [])) if tz.get("origin") else "") + ")."
           + (f" <span class='warn'>{_e(_tr(tz['finding'], lang))}</span>" if tz.get("finding") else ""))
    return (f"<h2>{_e(t['dbt'])}</h2><p>"
            + _h(t["dbt_head"], p=f"<code>{_e(d['projet'])}</code>", pr=f"<code>{_e(d['profil'])}</code>", c=f"<code>{_e(d['cible'])}</code>",
                                   a=_e(d["adaptateur"]), lect=_e(lect)) + "</p>"
            + "<p>" + _e(t["dbt_rel"].format(n=_n(len(d["copiees"]), lang), pol=poltxt)) + "</p>"
            + "<p>" + _h(t["dbt_rejeu"], i=_n(rj["identique"], lang), d=_n(rj["different"], lang), nc=_n(rj["non_compare"], lang),
                                            nr=_n(rj["non_rejouable"], lang), m=_n(rj["modeles"], lang), f="<code>replay-dbt.json</code>") + "</p>"
            + rj_proof
            + f"<p>{tzp}</p>"
            + (f"<table>{th(t['th_dbt_ex'])}{ex}</table>" if ex else "")
            + f"<h3>{_e(t['dbt_not'])}</h3><table>{th(t['th_dbt_nc'])}{nc or '<tr><td colspan=3>' + _e(t['none_f']) + '</td></tr>'}</table>")


def _body(res: dict, out: Path, lang: str, stamp: datetime) -> str:
    """The whole report body in one language: every section, same structure in English and French."""
    t = T[lang]
    s = res["summary"]
    n = lambda x: _n(x, lang)  # noqa: E731
    th = lambda cols: "<tr>" + "".join(f"<th>{_e(c)}</th>" for c in cols) + "</tr>"  # noqa: E731
    ok = s["parity"] == "complete"

    def result(c):
        prec = c.get("precision_cols") and c["rows_source"] == c["rows_target"] and c["fingerprint_ok"]
        return ("ok", t["r_match"]) if c["ok"] else (("warn", t["r_precision"]) if prec else ("ko", t["r_mismatch"]))

    def details(c):
        parts = [_tr(x, lang) for x in c["stats_diffs"][:3]]
        if c.get("within_precision_cols"):
            parts.append(t["within_prec"].format(cols=", ".join(c["within_precision_cols"])))
        return _e(t["sep"].join(parts))
    rows = "".join(
        f"<tr><td>{_e(c['table'])}</td><td>{n(c['rows_source'])}</td><td>{n(c['rows_target'])}</td>"
        f"<td class='{result(c)[0]}'>{_e(result(c)[1])}</td><td>{_e(proof.label(c.get('proof_level') or proof.FULL_AGGREGATES, lang))}</td>"
        f"<td>{details(c)}"
        f"{_e(t['buckets']) + _e(c['bad_buckets'][:5]) if c['bad_buckets'] else ''}</td></tr>"
        for c in res["checks"])
    rows += "".join(  # tables without a comparison: listed, with the reason, never left out
        f"<tr><td>{_e(x['table'])}</td><td>—</td><td>—</td><td class='ko'>{_e(t['r_not_compared'])}</td>"
        f"<td>{_e(proof.label(x['proof_level'], lang))}</td><td>{_e(_tr(x.get('proof_note') or '', lang))}</td></tr>"
        for x in res.get("not_compared") or [])
    qrows = "".join(
        f"<tr><td><code>{_e(q['sql'][:140])}</code></td><td class='{'ok' if q['status'] == 'match' else ('warn' if q['status'] == 'skipped' else 'ko')}'>"
        f"{_e(t['q_status'].get(q['status'], q['status']))}{_e(t['q_order']) if q['order_differs'] else ''}</td><td>{_e(_tr(q['detail'], lang))}</td></tr>"
        for q in res["queries"])
    crows = "".join(f"<tr><td>{_e(c['table'])}.{_e(c['column'])}</td><td>{_e(c['source_type'])} → {_e(c['target_type'])}</td>"
                    f"<td class='{'warn' if c['lossy'] else ''}'>{_e(_tr(c['note'], lang))}{_e(t['possible_loss']) if c['lossy'] else ''}</td></tr>"
                    for c in res["conversions"])
    nonp = res["scan"]["non_portable_columns"]
    objs = res["scan"]["objects_that_do_not_move"]
    objrows = "".join(f"<tr><td>{_e(t['objs'].get(k, k))}</td><td>{n(len(v))}</td><td>{_e(', '.join(v[:8]))}{'…' if len(v) > 8 else ''}</td></tr>"
                      for k, v in objs.items() if v)
    rto = s.get("rto")
    sy = synthese(res, lang)
    finds = "".join(f'<div class="find"><span class="tag {k}">{_e(tag)}</span><div><b>{_e(h)}</b><span>{_e(d)}</span></div></div>'
                    for k, tag, h, d in sy["findings"])
    meas = []
    if s["throughput_mb_s"]:
        meas.append(t["meas_tp"].format(v=n(s["throughput_mb_s"])))
    if rto:
        meas.append(t["meas_rto"].format(lo=n(rto["seconds"][0]), hi=n(rto["seconds"][1]), basis=_tr(rto["basis"], lang)))
    if s["warehouse_credits_used_est"] is not None:
        meas.append(t["meas_cr"].format(v=n(s["warehouse_credits_used_est"]))
                    + (t["meas_cost"].format(c=_fmt_cost(s["cost_usd_est"], lang), basis=_tr(s.get("cost_basis") or "", lang)) if s["cost_usd_est"] else ""))
    meas.append(t["meas_dur"].format(v=n(s["duration_s"])))
    loc = res["copy_location"]
    fp = _key_fingerprint(out) or t["key_na"]
    code = lambda x: f"<code>{_e(x)}</code>"  # noqa: E731
    seal = res["seal"]
    return f"""<h1>{_e(t['h1'])}</h1>
<p>{_h(t['meta'], src=code(_clean(res['source'])), dst=code(_clean(res['target'])), date=_e(_date(stamp, lang)), v=_e(res['env']['canot']))}</p>
<h2 style="border:0;margin-bottom:0">{_e(t['summary'])}</h2><p class="verdict"><b>{_e(sy['verdict'])}</b></p>{finds}
{share_block(drill_share(res), "drill-share.json", lang)}
<div class="box"><b>{_e(t['box'])}</b> <span class="{'ok' if ok else 'warn'}">{_e(t['box_ok'] if ok else t['box_ko'])}</span> —
{_e(t['box_d'].format(tok=n(s['tables_ok']), tt=n(s['tables_total']), lvl=proof.label(proof_levels(res)[0], lang).lower(), qm=n(s['queries_match']), qt=n(s['queries_total']),
                      qu=n(s['queries_untranslatable']), qx=n(s['queries_mismatch']), lc=n(s['lossy_conversions'])))}</div>
<h2>{_e(t['meas'])}</h2><ul>
{''.join(f'<li>{_e(m)}</li>' for m in meas)}</ul>
<p class="small">{_e(t['meas_note'])}</p>
<h2>{_e(t['where'])}</h2>
<p>{_h(t['where_d'], dst=code(_clean(loc['target'])), loc=code(_clean_path(loc['warehouse'])), cat=_e(_tr(loc['catalog'], lang)))}<br>
{_h(t['where_iceberg'], f=code(REREAD_FILE))}</p>
<h2>{_e(t['tables'])}</h2><table>{th(t['th_tables'])}{rows}</table>
{_proof_section(res, lang)}
<h2>{_e(t['queries'])}</h2><table>{th(t['th_queries'])}{qrows or '<tr><td colspan=3>' + _e(t['none_f']) + '</td></tr>'}</table>
{_dbt_section(res, lang)}
<h2>{_e(t['convs'])}</h2><table>{crows or '<tr><td>' + _e(t['none_f']) + '</td></tr>'}</table>
<h2>{_e(t['nomove'])}</h2>
<p>{_e(t['nomove_d'].format(n=n(len(nonp))))}</p>
<table>{th(t['th_objs'])}{objrows or '<tr><td colspan=3>' + _e(t['none_detected']) + '</td></tr>'}</table>
{regulatory_section("drill", lang, tag="div", anchor="regulatory-" + lang)}
<h2>{_e(t['limits'])}</h2>
<ul>{''.join(f'<li>{_e(x)}</li>' for x in t['limits_l'])}</ul>
<h2>{_e(t['tol'])}</h2><p>{code(json.dumps(res['tolerances'], ensure_ascii=False))}</p>
<h2>{_e(t['integ'])}</h2>
<p>{_h(t['integ_log'], n=n(seal['entries']), h=code(seal['last_hash']), cmd=code('canot verify-evidence ' + out.name))}</p>
<p>{_h(t['integ_key'], fp=code(fp))}
{_e(t['integ_sig'])}</p>
<p class="small"><i>{_e(t['disclaimer'])}</i></p>
<div class="cta">{_e(t['cta'])} <a href="{CONTACT_URL[lang]}">canot.io</a>.</div>"""


def write_report(res: dict, out: Path, lang: str | None = None) -> Path:
    """report.json (English) and report.html: both languages in one file; `lang` (or the locale) picks the pane shown first."""
    (out / "report.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False), encoding="utf-8", newline="\n")
    default = default_lang(lang)
    stamp = datetime.now()
    both = bilingual(_body(res, out, "en", stamp), _body(res, out, "fr", stamp), default)
    cut = both.index("</nav>") + len("</nav>")
    bar, panes = both[:cut], both[cut:]
    top = brand_bar("")
    top = top.replace('<span class="kind"></span>', bar) if '<span class="kind"></span>' in top else top + bar
    page = (f'<!doctype html><html lang="{default}"><meta charset="utf-8"><title>{_e(T[default]["title"])}</title>'
            f"<style>{CSS}{BRAND_CSS}</style>\n{top}\n{panes}\n</html>")
    write_relire_sql(res, out)
    p = out / "report.html"
    p.write_text(page, encoding="utf-8", newline="\n")
    return p

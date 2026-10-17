# Catalogue des écarts

Ce que deux moteurs rendent différemment pour la même requête ou le même type, **mesuré** avec Canot : la requête, le résultat de chaque côté, les versions et la date. Rien n'est ici supposé : ce qui n'est pas encore mesuré est listé à part.

## BigQuery : DATE_TRUNC sur une date rend une date, la copie rend un horodatage

*Mesuré le 13/10/2026. BigQuery (jeu public thelook_ecommerce) ; DuckDB 1.5.6 ; sqlglot 30.22 ; Canot 0.1.0.*

```sql
select date_trunc(date(created_at), month) as m, count(*) as orders
from `bigquery-public-data.thelook_ecommerce.orders` group by m order by m
```

| | |
|---|---|
| **Source** (BigQuery) | `2019-01-01 (type DATE)` |
| **Copie** (DuckDB sur la copie Iceberg) | `2019-01-01 00:00:00 (type TIMESTAMP)` |

**Pourquoi.** Dans cette mesure, la requête traduite rend un TIMESTAMP là où BigQuery rend une DATE. Les jours et les comptes sont identiques ; seul le type de la colonne change, ce qui peut casser une jointure ou un export qui attend une date.

**Que faire.** Écrire explicitement CAST(... AS DATE) dans la requête côté destination, puis rejouer l'exercice.

## Snowflake : la moyenne d'un montant décimal garde 8 décimales exactes, la copie la rend en flottant

*Mesuré le 16/10/2026. Snowflake (compte d'essai, TPC-H SF1) ; DuckDB 1.5.6 ; Canot 0.1.0.*

```sql
select l_returnflag, l_linestatus, avg(l_discount) as avg_disc
from tpch_sf1.lineitem group by l_returnflag, l_linestatus
```

| | |
|---|---|
| **Source** (Snowflake) | `0.04998530 (décimal, échelle 8)` |
| **Copie** (DuckDB sur la copie Iceberg) | `0.04998529583845148 (flottant)` |

**Pourquoi.** La moyenne d'un décimal reste décimale chez Snowflake, arrondie à 8 décimales ; DuckDB la calcule en flottant. L'écart relatif est d'environ 7e-8, au-dessus de la tolérance par défaut de Canot (1e-9).

**Que faire.** Décider d'une tolérance : `decimal_abs` ou `float_rel` dans le fichier de tolérances (`canot init-tolerances`), ou arrondir dans la requête. Un montant comptable mérite une tolérance écrite et signée par le métier.

## Les nanosecondes ne survivent pas à Iceberg v2

*Mesuré le 17/10/2026. DuckDB 1.5.6 ; pyiceberg 0.12 ; Canot 0.1.0 (`canot demo`).*

```sql
select min(scanned_at), max(scanned_at) from distrib.tickets
```

| | |
|---|---|
| **Source** (Tout moteur à horodatage nanoseconde (par exemple DuckDB TIMESTAMP_NS, Snowflake TIMESTAMP(9))) | `2026-03-01 08:00:10.000000311` |
| **Copie** (Apache Iceberg v2) | `2026-03-01 08:00:10` |

**Pourquoi.** Les horodatages d'Iceberg v2 s'arrêtent à la microseconde : la copie arrondit les valeurs en dessous. Ce n'est pas une corruption, c'est une limite du type d'arrivée. La spécification Iceberg v3 prévoit un horodatage à la nanoseconde ; son support dépend de chaque moteur et de chaque version.

**Que faire.** Décider si la précision sous la microseconde compte (journaux de capteurs, trading, traçabilité). Si oui, stocker l'horodatage en texte ou en entier (nanosecondes depuis 1970) à côté, ou viser un moteur compatible Iceberg v3.

## À mesurer

Pas encore mesuré : ne rien en conclure.

- **Fuseaux horaires** : TIMESTAMP_TZ de Snowflake, TIMESTAMP de BigQuery (UTC) : le décalage d'origine est-il conservé ?
- **Tri des valeurs nulles** : Chaque moteur place les NULL en tête ou en fin par défaut : un ORDER BY peut rendre un autre ordre.
- **Casse et collation** : Comparaisons et tris de texte selon la collation de la plateforme.
- **Semi-structuré** : VARIANT (Snowflake), JSON (BigQuery), STRUCT et ARRAY : forme du texte et ordre des clés.
- **Grands décimaux** : NUMBER(38) de Snowflake, BIGNUMERIC de BigQuery : précision conservée jusqu'où ?
- **Géographie** : GEOGRAPHY / GEOMETRY : format de sérialisation d'arrivée.

## Proposer un écart

Ouvrez une issue avec : la requête ou le type, le résultat de chaque moteur, les versions, la date. Un écart sans résultat reproductible n'entre pas dans le catalogue.

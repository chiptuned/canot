# Arrivée sur site

Canot écrit la copie au format Apache Iceberg. L'arrivée peut être un dossier de votre réseau, ou un stockage compatible S3 que vous hébergez.

| Destination | Commande | État |
|---|---|---|
| Dossier local ou monté (NAS, disque, partage) | `--to /chemin/vers/le/dossier` | testé |
| Stockage compatible S3 hébergé chez vous | `--to "s3://coffre/canot?endpoint=https://s3.interne:9000&region=us-east-1"` | testé sur un serveur S3 local (moto) |
| Catalogue personnalisé (SQLite ailleurs) | `--catalog sqlite:///chemin/catalogue.db` | testé |
| Catalogue PostgreSQL | `--catalog env:CANOT_CATALOG_URI` avec `pip install 'canot[postgres]'` | branché, pas encore testé contre un vrai PostgreSQL |
| Proxy d'entreprise | `...?proxy=http://proxy.interne:3128` dans l'URL S3 | branché, pas encore testé |
| Autorité de certification interne | `...?ca=/chemin/ca.pem` dans l'URL S3 (transmise par `AWS_CA_BUNDLE`) | branché, pas encore testé |

Les clés du stockage passent par `CANOT_S3_ACCESS_KEY` et `CANOT_S3_SECRET_KEY` (ou la configuration `scw`). Un mot de passe de catalogue se met dans une variable d'environnement, jamais sur la ligne de commande : `--catalog env:NOM_DE_LA_VARIABLE`.

## Relire la copie sans Canot

Chaque exercice écrit un fichier `relire.sql` à côté du rapport. Il contient les commandes DuckDB qui comptent les lignes de chaque table copiée :

```bash
duckdb < canot-out/relire.sql
```

La copie ne dépend donc pas de l'outil : n'importe quel moteur qui lit Iceberg (DuckDB, Spark, Trino) peut la lire. Pour un stockage S3, créez d'abord un secret DuckDB avec vos clés (voir le fichier).

## Ce qui n'est pas pris en charge

Écrire directement dans PostgreSQL, ClickHouse ou un autre moteur de base : la copie reste en Iceberg, que ces moteurs savent lire. Pas d'installation hors ligne documentée pour l'instant.

## Nettoyer

```bash
canot teardown --to "s3://coffre/canot?endpoint=https://s3.interne:9000&region=us-east-1"
```

# Canot : les commandes

Canot répète votre départ d'une plateforme de données, dans vos propres comptes. Une commande suffit :

```bash
uvx canot drill SOURCE DESTINATION
```

`SOURCE` est la plateforme à quitter (Snowflake, Databricks, BigQuery), `DESTINATION` l'endroit où la copie est écrite. Les valeurs en `MY_…` sont à remplacer par les vôtres. Canot copie les tables en Apache Iceberg, compare tout, rejoue vos requêtes les plus coûteuses et écrit un dossier de preuve signé. Il ne vous envoie rien : pas de télémétrie.

```bash
# Snowflake vers un dossier local
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg

# Databricks vers un stockage S3 de votre choix
uvx canot drill "databricks://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG" "s3://my-bucket/canot?endpoint=https://s3.internal:9000&region=us-east-1"

# BigQuery vers un hébergeur européen
uvx canot drill "bigquery://MY_PROJECT/MY_DATASET?location=EU" scaleway:fr-par --bucket my-bucket
```

Les connecteurs Snowflake, BigQuery et Databricks sont installés avec Canot : rien à ajouter. Sans `uv`, `pipx run canot …` ou `pip install canot` font de même.

## Les commandes

| Commande | Ce qu'elle fait |
|---|---|
| `canot scan SOURCE` | Radiographie en lecture seule : inventaire, volumes, consommation, ce qui ne part pas, rejouabilité des requêtes. Écrit `scan.html` (à transmettre), `scan.json` et des CSV. Voir [scan](scan.md). |
| `canot drill SOURCE DESTINATION` | Exercice de sortie complet : copie, comparaison table par table, rejeu des requêtes, dossier de preuve signé. |
| `canot demo` | Démonstration de 30 secondes, sans compte ni données : le jeu TPC-H (référence des bancs d'essai de bases de données, généré en local, 100 Mo par défaut ; `--sf 1` pour environ 1 Go) et un exercice complet. Elle affiche les deux commandes qu'elle lance (`demo-data`, `drill`) et accepte une destination : `canot demo aws:eu-west-3 --bucket MON-COFFRE`. Hors ligne, un jeu fictif de secours. |
| `canot verify-evidence DOSSIER` | Vérifie la chaîne et la signature d'un dossier de preuve. `--expect-pub` refuse un dossier signé par une autre clé. |
| `canot teardown DESTINATION` | Supprime les données copiées. |
| `canot snowflake-setup` | Écrit le script SQL d'accès en lecture seule à faire relire et lancer par un administrateur. `--generate-key` crée la paire de clés. |
| `canot bigquery-setup` | Écrit le script `gcloud` du rôle personnalisé en lecture seule. |
| `canot init-tolerances` | Écrit un fichier de tolérances de comparaison à relire avec vous. |
| `canot demo-data CHEMIN` | Crée une base DuckDB de démonstration. |
| `canot rejeu-dbt dbt://PROJET --base COPIE.duckdb` | Preuve P3 sur un projet dbt : rejoue les modèles SQL dans DuckDB à partir de la copie des sources (base DuckDB, ou `--lac` écrit par `canot drill`), compare chaque sortie à la table d'origine (lignes, statistiques, empreinte) et signe un dossier. Voir [scan](scan.md#preuve-p3--canot-rejeu-dbt). |
| `canot connecteurs` | Liste les plateformes connues : forme d'URL, inventaire (`scan`), export (`drill`), forme de preuve, statut (testé ou écrit, non testé) et extra `pip` à installer. |
| `canot mcp` | Serveur MCP : la documentation et l'explication de vos rapports, pour un agent. Voir [Avec un agent](#avec-un-agent-mcp). |

Toutes les commandes acceptent `--debug` (trace complète en cas d'erreur).

## `canot drill`

Options utiles :

| Option | Rôle |
|---|---|
| `--bucket NOM` | Obligatoire avec un raccourci d'hébergeur (`scaleway:fr-par`, `ovh:gra`, `outscale:eu-west-2`, `ionos:eu-central-1`, `stackit:eu01`, `aws:eu-west-3`). |
| `--catalog URI` | Catalogue Iceberg : `sqlite:///…`, ou `env:NOM_DE_VARIABLE` pour PostgreSQL (`canot[postgres]`). Par défaut : un fichier SQLite dans le dossier de sortie. |
| `--out DOSSIER` | Où écrire le rapport et le dossier de preuve (défaut `canot-out`). |
| `--tables SCHEMA.TABLE …` | Limiter à certaines tables. |
| `--queries FICHIER.sql` | Rejouer vos propres requêtes. Par défaut : les plus coûteuses de l'historique de la plateforme. |
| `--top N` | Nombre de requêtes de l'historique à rejouer (défaut 20). |
| `--tolerances FICHIER.toml` | Tolérances de comparaison (arrondis, horodatages). |
| `--credit-price USD` | Prix du crédit Snowflake de votre contrat. Sans lui : une fourchette du prix catalogue, datée. |
| `--replay-max-gb N` | Taille maximale d'une table chargée en mémoire pour rejouer les requêtes (défaut 2). |
| `--key CHEMIN` | Clé de signature (défaut `~/.canot/evidence.key`, ou la variable `CANOT_EVIDENCE_KEY_PATH`). |

Résultat dans `--out` : `report.html` (lisible par un comité), `report.json`, le dossier de preuve (`evidence.jsonl`, `evidence.sig`, `evidence.pub`) et `relire.sql`. La signature atteste que le dossier n'a pas été modifié depuis sa signature. Elle n'atteste ni l'auteur ni la date.

## `canot scan`

```bash
uvx canot scan "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" --level 2
```

`--level 1|2|3` (défaut 3) règle jusqu'où lire ; `--credit-price` remplace le prix catalogue ; `--include-sql-text` écrit aussi le SQL brut dans un fichier à part (jamais par défaut). Détail des lectures, des droits et des garanties : [scan](scan.md).

## Les sources

| Source | Forme | Authentification |
|---|---|---|
| BigQuery | `bigquery://PROJET/dataset1,dataset2?location=EU` ; sans dataset : tous ceux du projet ; autre projet : `bigquery://FACTURATION/projet-donnees.dataset` | identifiants applicatifs Google (`gcloud auth application-default login`) |
| Snowflake | `snowflake://COMPTE/BASE?user=…&warehouse=…&role=…&schemas=A,B` | paire de clés : variable `SNOWFLAKE_PRIVATE_KEY_PATH` (recommandé) ou `SNOWFLAKE_PASSWORD` |
| Databricks | `databricks://HÔTE/CHEMIN_HTTP_DE_L_ENTREPÔT?catalog=…` | variable `DATABRICKS_TOKEN` (jeton en lecture) ; connecteur non encore testé sur un vrai compte |
| DuckDB | `maison.duckdb` | aucune |
| dbt (projet) | `dbt://CHEMIN_DU_PROJET` : le projet, son dossier `target/` ou `manifest.json` ; scan seulement (artefacts lus hors ligne), preuve par `canot rejeu-dbt` | aucune : ni réseau, ni commande dbt |

`canot connecteurs` affiche, pour chacune, ce que Canot sait faire (inventaire complet ou léger, export, preuve) et si c'est testé. Pour dbt, la preuve P3 passe par `canot rejeu-dbt`.

## Sortie en local ou on-prem

Pas de cloud à l'arrivée : un dossier suffit.

```bash
uvx canot drill "snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER" ./iceberg
```

Canot écrit la copie au format Iceberg dans `./iceberg` et le dossier de preuve dans `canot-out/`. La copie ne dépend pas de l'outil : DuckDB, Spark ou Trino la lisent. Le fichier `relire.sql` donne les commandes DuckDB pour recompter chaque table :

```bash
duckdb < canot-out/relire.sql
```

| Arrivée | Commande |
|---|---|
| Dossier local ou monté (disque, NAS, partage) | `canot drill SOURCE /chemin/vers/le/dossier` |
| Stockage compatible S3 chez vous (MinIO, Ceph, NetApp…) | `canot drill SOURCE "s3://coffre/canot?endpoint=https://s3.interne:9000&region=us-east-1"` |
| Proxy d'entreprise, autorité de certification interne | ajoutez `&proxy=http://proxy.interne:3128` et `&ca=/chemin/ca.pem` à l'URL S3 |
| Catalogue PostgreSQL | `--catalog env:CANOT_CATALOG_URI` (mot de passe dans la variable, jamais sur la ligne de commande) |

Les clés du stockage passent par `CANOT_S3_ACCESS_KEY` et `CANOT_S3_SECRET_KEY` (ou la configuration `scw`). État de chaque cas (testé ou seulement branché) : [sur-site](sur-site.md).

## Les hébergeurs

`scaleway:fr-par`, `ovh:gra`, `outscale:eu-west-2`, `ionos:eu-central-1`, `stackit:eu01`, `aws:eu-west-3`, avec `--bucket`. Vérifiez chaque point de terminaison dans la console de l'hébergeur. La qualification SecNumCloud, quand elle compte pour vous, dépend de l'offre précise : voir [cibles](cibles-ue.md).

## Avec un agent (MCP)

Beaucoup d'équipes travaillent avec un agent. `canot mcp` lui donne la documentation de Canot et lui explique vos rapports, sans rien envoyer à personne : le serveur tourne sur votre machine.

Installation dans Claude Code :

```bash
claude mcp add canot -- uvx canot mcp
```

Ou dans la configuration MCP de votre client (Claude Desktop, Cursor, VS Code…) :

```json
{
  "mcpServers": {
    "canot": { "command": "uvx", "args": ["canot", "mcp"] }
  }
}
```

Outils exposés :

| Outil | Ce qu'il fait |
|---|---|
| `commandes` | Liste les commandes et leurs options. |
| `chercher_doc(requete)` | Cherche dans la documentation et rend les passages. |
| `lire_doc(sujet)` | Rend une page entière : `commandes`, `scan`, `sur-site`, `cibles`, `ecarts`. |
| `capacites(plateforme)` | Ce que Canot sait faire sur chaque plateforme (inventaire, export, forme de preuve), avec le statut (testé ou écrit, non testé) et la date de test consignée. Sans argument : toutes les plateformes. |
| `expliquer_scan(chemin)` | Lit un `scan.json` et le résume : constats, lectures non faites, chiffres estimés, ce qui est à examiner. |
| `expliquer_rapport(chemin)` | Lit un `report.json` d'exercice : écarts, tables et requêtes en cause, causes connues du catalogue. Lit aussi un `rejeu-dbt.json` (preuve P3 dbt) : modèles identiques, différents, non rejouables et leurs raisons. |

Les outils lisent des fichiers que vous désignez. Ils n'ouvrent aucune connexion à vos plateformes.

Pour les agents qui lisent le web : [canot.io/llms.txt](https://canot.io/llms.txt) et [canot.io/llms-full.txt](https://canot.io/llms-full.txt).

## Pour aller plus loin

- [scan](scan.md) : ce qui est lu, avec quels droits, et les garanties.
- [sur-site](sur-site.md) : arrivée locale ou sur votre stockage.
- [cibles](cibles-ue.md) : hébergeurs et pays.
- [ecarts](ecarts.md) : ce que deux moteurs rendent différemment, mesuré.
- Une aide pour l'exécuter sur votre périmètre : [canot.io](https://canot.io/#contact).

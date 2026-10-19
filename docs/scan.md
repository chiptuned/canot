# `canot scan` : ce qui est lu, avec quels droits

Le scan produit `scan.html` (à transmettre), `scan.json` et des CSV. Il décrit votre plateforme ; il ne recommande ni de partir ni de rester.

## Garanties

- **Lecture seule.** (Projet dbt : aucune requête, des fichiers lus, voir [dbt](#dbt).) Chaque requête passe par une garde avant d'être envoyée : seuls `SELECT`, `SHOW`, `DESCRIBE` et `ALTER SESSION SET` sont admis. Tout le reste est refusé sans rien envoyer. Les fonctions `SYSTEM$` sont refusées aussi.
- **Jamais le contenu des tables.** Le scan lit des métadonnées et l'historique d'exécution. Il ne demande aucun droit de lecture sur les données.
- **Le SQL brut n'est pas écrit.** Les littéraux sont masqués (`WHERE email = ?`), les requêtes qui contiennent un mot de passe ou un secret sont comptées puis jetées. Le texte brut n'est écrit qu'avec `--include-sql-text`, dans `scan-sql-brut.json` (droits 0600), et le rapport le signale.
- **Identités pseudonymisées.** Les utilisateurs et rôles de requêtes sont remplacés par un pseudonyme (HMAC, sel tiré à chaque scan et jamais écrit). Les e-mails, identifiants de connexion et adresses IP ne sont jamais lus. Les chemins de stockage sont réduits au nom du compartiment.
- **Aucune lecture silencieusement perdue.** Chaque lecture est consignée « lu » ou « non lu » avec sa raison. Une valeur non lue reste vide, jamais 0.

## Niveaux (`--level`, 3 par défaut)

| Niveau | Contenu | Droits en plus |
|---|---|---|
| 1 | Inventaire, types de colonnes, volumes, historique facturé, consommation, objets (vues, routines, tâches, stages, partages…), dépendances | métadonnées et consommation |
| 2 | + formes de requêtes normalisées et leur rejouabilité, lectures et écritures par table, politiques | accès à l'historique des requêtes |
| 3 | + rôles propriétaires, outils clients, types d'utilisateurs, capacités réservées | droits et sessions |

Ce que vos droits ne permettent pas est consigné « non lu » dans le rapport (constat F12) : le scan lit ce qui lui est ouvert et dit ce qu'il n'a pas vu.

## Snowflake

`canot snowflake-setup --generate-key --base MA_BASE` écrit le script à relire et à lancer par un administrateur. Il crée un rôle, un entrepôt XS qui se suspend après 60 s, un plafond de crédits, et accorde les rôles de base de Snowflake :

| Rôle de base | Niveau | Sert à |
|---|---|---|
| `SNOWFLAKE.OBJECT_VIEWER` | 1 | tables, colonnes, vues, routines, tâches, stages, dépendances |
| `SNOWFLAKE.USAGE_VIEWER` | 1 | volumes, crédits, historique, chargements |
| `SNOWFLAKE.GOVERNANCE_VIEWER` | 2 | historique des requêtes, lectures par table, politiques, étiquettes |
| `SNOWFLAKE.SECURITY_VIEWER` | 3 | rôles, utilisateurs (nom et type seulement), sessions |

Jamais demandés : `IMPORTED PRIVILEGES`, `MANAGE GRANTS`, `IMPORT SHARE`, `USAGE` sur les fonctions, `READ` sur les stages.

Limites connues : `ACCOUNT_USAGE` a une latence de 45 minutes à 3 heures (jusqu'à 2 jours pour l'historique de chargement). Le rapport affiche la latence de chaque lecture. `ACCESS_HISTORY` exige l'édition Enterprise ; sans elle, les lectures par table se déduisent de `TABLE_QUERY_PRUNING_HISTORY` avec une confiance moyenne.

## BigQuery

`canot bigquery-setup --project MON_PROJET --member user:…` écrit le script `gcloud` d'un rôle personnalisé `canotScanner`. Permissions obligatoires : `bigquery.jobs.create`, `bigquery.jobs.listAll`, `bigquery.datasets.get`, `bigquery.tables.list`, `bigquery.tables.get`, `bigquery.tables.getIamPolicy`, `bigquery.routines.list`, `bigquery.routines.get`, `bigquery.models.list`, `bigquery.models.getMetadata`, `bigquery.reservations.list`, `bigquery.reservationAssignments.list`, `bigquery.capacityCommitments.list`, `bigquery.bireservations.get`, `bigquery.config.get`. Optionnelles : `bigquery.rowAccessPolicies.list`, `bigquery.transfers.get`, `bigquery.connections.list`, `bigquery.dataPolicies.list`.

Jamais : `bigquery.tables.getData`, ni les rôles `bigquery.user` ou `jobUser`.

Points de méthode :
- Une requête `INFORMATION_SCHEMA` par région (les régions ne sont pas hiérarchiques : `EU` ne couvre pas `europe-west1`). BigQuery facture au moins 10 Mo par requête de ce type, sans cache : le scan plafonne chaque requête et fait un essai à blanc avant.
- Les volumes viennent de `TABLE_STORAGE` quand elle est lisible, sinon de l'API `tables.get`. Canot ne modifie jamais le paramétrage du projet pour l'activer.
- Sous une politique de lignes, `total_bytes_billed` est vide : le rapport le dit au lieu d'afficher 0.
- Le palier gratuit est partagé par le compte de facturation : il n'est pas imputable par projet.

## dbt

`canot scan dbt://CHEMIN_DU_PROJET` lit les artefacts du projet, sans réseau ni commande dbt : `target/manifest.json` (obligatoire), `target/run_results.json` et `target/catalog.json` s'ils existent. `CHEMIN` peut être le projet, son dossier `target/` ou le fichier `manifest.json` ; un dossier de projet passé sans `dbt://` est reconnu aussi. Sans manifest, `dbt parse` le produit en lisant les fichiers du projet, sans se connecter à l'entrepôt. Un seul niveau (1) : `--level` est ramené à 1.

| Lu | Ce qu'on garde |
|---|---|
| `manifest.json` | modèles (matérialisation, relation cible, dépendances), tests, sources, seeds, snapshots, expositions, métriques, macros du projet, paquets (nombre de macros), adaptateur et version de dbt, graphe des dépendances (`liens` : chaque `ref()` et `source()`) |
| SQL compilé des modèles, tests, snapshots | empreinte sha256 de la forme normalisée (littéraux masqués), nombre de lignes, fonctions et constructions propres au dialecte de l'adaptateur (sqlglot), fonctions absentes de DuckDB, catégorie de traduction vers DuckDB (C0 à C7, mêmes règles que les requêtes) ; jamais le texte |
| `run_results.json` | état de la dernière invocation par nœud, échecs, lignes en échec des tests, date de dernière écriture des tables |
| `catalog.json` | colonnes et types (squelette pour l'EXPLAIN DuckDB), lignes et octets si l'adaptateur les donne, propriétaire pseudonymisé |

Portabilité d'un traitement : `traduisible` quand sqlglot traduit le SQL vers DuckDB (accepté par EXPLAIN si `catalog.json` est lu, « non testé » sinon) ; `a_reconstruire` seulement sur un fait lu : fonction absente de DuckDB, traduction impossible, matérialisation propre à l'adaptateur (par exemple `dynamic_table`) ; `non_determine` pour un modèle Python, un SQL non compilé encore plein de Jinja, ou un SQL qui contient un secret (compté puis écarté).

Constats : F13 pour un traitement à reconstruire (avec la raison), F08 pour un modèle dont le SQL dépend de Jinja et de macros (blocs `{% %}`, appels autres que `ref`, `source`, `config` ; les tests génériques de dbt ne comptent pas), F11 pour le DAG du projet (dernière invocation, échecs ; planification non lue : elle vit dans l'orchestrateur), F01 pour une source externe (emplacement réduit au compartiment), F12 pour un artefact absent. Consommation, volumes facturés et droits ne sont pas dans ces fichiers : le rapport les dit « non lus ».

Validé le 19/10/2026 sur le projet du banc `lab/dbt` (artefacts d'un `dbt build` réel, dbt 1.12.6, adaptateur Snowflake) : 8 modèles (4 vues staging, 4 tables marts), 8 tests, 4 sources, 19 liens ; constats F11 et F12 (pas de `catalog.json`). Copie de ces artefacts et sorties figées : `tests/fixtures/dbt/banc`, `tests/golden/cli_scan_dbt`.

### Preuve P3 : `canot rejeu-dbt`

`canot rejeu-dbt dbt://PROJET --base copie.duckdb` (ou `--iceberg DESTINATION --drill-out DOSSIER` pour la copie Iceberg écrite par `canot drill`) rejoue chaque modèle SQL dans un DuckDB local, dans l'ordre du graphe, à partir des tables sources de la copie, puis compare sa sortie à la table que la plateforme avait matérialisée, avec les critères de `canot drill` : nombre de lignes, statistiques par colonne, empreinte de contenu. Les types de la sortie rejouée sont alignés sur ceux de la table d'origine avant la comparaison ; chaque conversion est consignée. Le résultat (`rejeu-dbt.json`) et le dossier signé (`evidence.jsonl`, à vérifier par `canot verify-evidence`) classent chaque modèle :

| Statut | Sens |
|---|---|
| `identique` | rejoué, comparé, aucun écart dans les tolérances |
| `different` | rejoué, comparé, écart (lignes, statistiques ou empreinte) |
| `non_compare` | rejoué, mais aucune table d'origine dans la copie (une vue) ; sa sortie alimente les modèles suivants |
| `non_rejouable` | avec la raison : langage Python, incrémental (dépend de l'état antérieur), éphémère (rejoué à travers ses utilisateurs), fonction propre ou matérialisation propre à l'adaptateur, traduction refusée, table absente de la copie, dépendance non rejouée |

Il faut le SQL compilé dans le manifest (`dbt compile` ou `dbt build` l'y met). Testé le 19/10/2026 sur les artefacts du banc et une copie DuckDB construite par les tests (tables d'origine écrites à la main, sans réseau) : 4 tables marts identiques, 4 vues rejouées non comparées, écart injecté détecté, y compris en lisant le lac écrit par `canot drill`.

Premier rejeu sur une copie réelle (19/10/2026) : `canot drill` du compte Snowflake du banc (schémas RAW et marts) vers une copie Iceberg local, puis `canot rejeu-dbt --lac`, sans nouvelle requête vers la plateforme. Résultat : `int_order_totals` identique, 4 vues rejouées non comparées, 3 marts différents (`dbt_fct_orders` : 150 000 lignes à l'origine, 25 325 rejouées ; `dbt_customer_ltv` ; `dbt_daily_revenue`). Ces écarts sont justes : la table des clients est protégée par une politique de lignes et le rôle de lecture du drill n'en voit qu'une partie. La copie des entrées est incomplète et le rejeu le montre : c'est exactement ce que P3 doit détecter (le constat F10 du scan Snowflake rappelle qu'une copie demande un principal non filtré par ces politiques).

Le même essai a montré un faux écart, corrigé depuis : `int_order_totals` était classé différent alors que lignes et statistiques étaient égales. Cause, colonne par colonne : `ORDER_ID` et `ITEMS` égaux (la conversion consignée `ITEMS : HUGEINT -> INTEGER` ne change aucune valeur) ; `BASKET_VALUE` est une somme de flottants (`UNIT_PRICE` en FLOAT) que Snowflake et DuckDB additionnent dans un ordre différent. 36 971 valeurs diffèrent au dernier bit (`121.79` contre `121.79000000000002`, écart relatif au plus 4e-16), et l'empreinte de contenu hache le texte exact. Désormais, quand lignes et statistiques sont égales et que seule l'empreinte diffère, le rejeu recalcule l'empreinte des deux côtés avec les colonnes flottantes arrondies à 12 chiffres significatifs (plus fin que la tolérance relative de 1e-9). Si elle est alors égale, le modèle est « identique dans la tolérance » et le rapport consigne les colonnes arrondies, le nombre de valeurs différentes au bit et l'écart relatif maximal mesuré (`verification.empreinte_flottants`). Un écart plus grand reste « différent ».

## Lire les chiffres

Chaque chiffre porte sa nature : **mesuré** (lu tel quel), **calculé** (déduit de mesures, formule donnée), **estimé** (fourchette et hypothèses), **non lu** ou **non estimé** (avec la raison). Les prix sont des repères datés et sourcés ; passez `--credit-price` avec le prix de votre contrat pour les remplacer. Le coût de lecture et la durée d'une copie sont des estimations de planification : `canot drill` les remplace par des mesures.

La rejouabilité mesure combien de formes de requêtes sont traduites vers DuckDB **et acceptées** par lui, sur un squelette vide typé à partir de vos colonnes. **Accepté ne veut pas dire résultat identique** : seul `canot drill` compare les résultats. Les requêtes sur des tables hors du périmètre lu, les requêtes dont la plateforme ne donne pas le texte et les requêtes de métadonnées sont comptées à part, hors taux.

## Ce que le scan ne voit pas

Les lectures par l'API Storage Read et les requêtes lancées depuis d'autres projets (BigQuery), les consommateurs de partages et `RESULT_SCAN` (Snowflake), les traitements plus rares que la fenêtre lue (365 jours sur Snowflake, 90 à 180 jours sur BigQuery). La fenêtre réelle est affichée à côté de chaque constat.

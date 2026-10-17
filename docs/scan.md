# `canot scan` : ce qui est lu, avec quels droits

Le scan produit `scan.html` (à transmettre), `scan.json` et des CSV. Il décrit votre plateforme ; il ne recommande ni de partir ni de rester.

## Garanties

- **Lecture seule.** Chaque requête passe par une garde avant d'être envoyée : seuls `SELECT`, `SHOW`, `DESCRIBE` et `ALTER SESSION SET` sont admis. Tout le reste est refusé sans rien envoyer. Les fonctions `SYSTEM$` sont refusées aussi.
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

## Lire les chiffres

Chaque chiffre porte sa nature : **mesuré** (lu tel quel), **calculé** (déduit de mesures, formule donnée), **estimé** (fourchette et hypothèses), **non lu** ou **non estimé** (avec la raison). Les prix sont des repères datés et sourcés ; passez `--credit-price` avec le prix de votre contrat pour les remplacer. Le coût de lecture et la durée d'une copie sont des estimations de planification : `canot drill` les remplace par des mesures.

La rejouabilité mesure combien de formes de requêtes sont traduites vers DuckDB **et acceptées** par lui, sur un squelette vide typé à partir de vos colonnes. **Accepté ne veut pas dire résultat identique** : seul `canot drill` compare les résultats. Les requêtes sur des tables hors du périmètre lu, les requêtes dont la plateforme ne donne pas le texte et les requêtes de métadonnées sont comptées à part, hors taux.

## Ce que le scan ne voit pas

Les lectures par l'API Storage Read et les requêtes lancées depuis d'autres projets (BigQuery), les consommateurs de partages et `RESULT_SCAN` (Snowflake), les traitements plus rares que la fenêtre lue (365 jours sur Snowflake, 90 à 180 jours sur BigQuery). La fenêtre réelle est affichée à côté de chaque constat.

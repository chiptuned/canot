# Banc de test

Un entrepôt de données volontairement complexe, pour valider `canot scan` et `canot drill` sans client.

- `bigquery_lab.py PROJET` : 5 jeux de données (brut, préparation, modèle, ML, externe) dans un projet personnel, niveau gratuit. Tables partitionnée et clusterisée, STRUCT/ARRAY/JSON/GEOGRAPHY/NUMERIC/BIGNUMERIC, vues, vue matérialisée, snapshot, clone, politique d'accès aux lignes, table externe, routines SQL et JavaScript, modèle BigQuery ML, charge de travail étiquetée.
- `snowflake_lab.py COMPTE` : base CANOT_LAB dans un compte d'essai. VARIANT/OBJECT/ARRAY/GEOGRAPHY/TIMESTAMP_TZ, clustering, vue sécurisée, vue matérialisée, table dynamique, routines SQL/JavaScript/Python, flux, tâches, stage externe et intégration (factices), politiques de masquage et d'accès, étiquettes, rôles, partage sortant, charge étiquetée. Chaque création est tentée séparément : ce qui exige une édition supérieure est signalé puis sauté.
- `dbt/` : un projet dbt (préparation, modèles, tests) qui tourne sur les deux plateformes et produit le trafic réaliste, avec les commentaires JSON que dbt ajoute à ses requêtes.

Rien n'est stocké ici de secret : l'authentification passe par les identifiants applicatifs Google et par une clé Snowflake locale.

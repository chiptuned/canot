# canot

**Prouvez que vous pouvez partir.** · *Prove you can leave.*

Canot fait un exercice de sortie de Snowflake ou Databricks : il copie vos données vers Apache Iceberg sur un stockage que vous contrôlez
(Scaleway, OVHcloud, Outscale, IONOS, STACKIT, AWS…), rejoue vos requêtes clés des deux côtés, compare les résultats et produit un dossier de preuve signé.
Tout tourne dans vos comptes. Canot n'envoie rien à personne (pas de télémétrie).

```bash
uvx canot scan  --from snowflake://MONORG-MONCOMPTE/MABASE?user=CANOT_USER     # gratuit, lecture seule
uvx canot drill --from snowflake://MONORG-MONCOMPTE/MABASE?user=CANOT_USER \
                --to scaleway:fr-par --bucket mon-bucket --queries mes_requetes.sql
```

## Ce que ça fait

1. `scan` : tables, volumes, colonnes à types propres à la plateforme, objets qui ne partent pas seuls (vues, procédures, tâches…), estimation de la durée et du coût de sortie.
2. `drill` : copie en Iceberg v2, puis compare table par table (schéma, nombre de lignes, min/max/sommes par colonne, empreinte de chaque ligne), puis rejoue vos requêtes sur la source et sur la copie (traduites avec SQLGlot) et compare les résultats avec des tolérances écrites à l'avance.
3. Le dossier (`report.html`, `report.json`, `evidence.jsonl`) contient la parité, les conversions de types avec perte possible, le débit mesuré, un temps de reprise en fourchette et le coût estimé. Le journal est chaîné par empreintes et signé (Ed25519) avec une clé créée sur place : `canot verify-evidence <dossier>`.

Ce n'est pas une certification et ça ne dit rien de la conformité juridique de vos contrats.

## État

| Brique | État |
|---|---|
| Source DuckDB (démo, tests) | testée de bout en bout, voir `tests/` |
| Cible dossier local, Iceberg v2 | testée |
| Source Snowflake | écrite, **pas encore testée sur un vrai compte** |
| Source Databricks | écrite, **pas encore testée sur un vrai compte** |
| Cibles S3 (Scaleway, OVH…) | écrites, test sur Scaleway à faire |
| Module OpenTofu Scaleway | validé (`tofu validate`), jamais appliqué |

## Essayer en 2 minutes, sans compte

```bash
canot demo-data demo.duckdb
canot drill --from demo.duckdb --to ./lac --queries demo.queries.sql
open canot-out/report.html && canot verify-evidence canot-out
```

## Accès Snowflake en lecture seule

`canot snowflake-setup` affiche un script SQL à relire et à lancer par un administrateur : un rôle en lecture seule, un entrepôt XS qui se met en pause après 60 s,
un plafond de 25 crédits, un utilisateur à clé publique. Puis `export SNOWFLAKE_PRIVATE_KEY_PATH=...`. Les identifiants ne passent que par des variables d'environnement.

## Cibles

Voir [docs/cibles-ue.md](docs/cibles-ue.md) : hébergeurs français et européens, qualification SecNumCloud, pays visés.

## Licence

Apache-2.0. Les fonctions de l'offre payante **Canot Vigie** (dossiers signés avec modèles réglementaires, exercices planifiés, support) arrivent séparément.

## Sources prises en charge

`duckdb://` (démo), `snowflake://`, `bigquery://PROJET/DATASET?location=EU`, `databricks://`. BigQuery : `pip install 'canot[bigquery]'`, identifiants Google applicatifs.
Livrables par offre : [docs/livrables.md](docs/livrables.md). Exemple chiffré sur données inventées : `examples/distributeur-fictif/`.

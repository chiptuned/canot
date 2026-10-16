<h1 align="center">canot</h1>
<p align="center"><b>Prouvez que vous pouvez partir.</b><br><i>Prove you can leave.</i></p>

<p align="center">
  Exercice de sortie pour Snowflake, BigQuery et Databricks : copie vers Apache Iceberg sur un cloud que vous contrôlez,
  comparaison ligne par ligne, dossier de preuve signé.<br>
  Tout tourne dans vos comptes. Aucune télémétrie.
</p>

```bash
uvx canot scan  --from "bigquery://MON-PROJET/mon_dataset?location=EU"        # gratuit, lecture seule
uvx canot drill --from "bigquery://MON-PROJET/mon_dataset?location=EU" \
                --to scaleway:fr-par --bucket mon-bucket --queries mes_requetes.sql
```

---

## Pourquoi

Les régulateurs demandent de plus en plus aux entreprises de **pouvoir prouver** qu'elles peuvent changer de fournisseur :
DORA (plans de sortie testés pour la finance), Data Act (plus aucun frais de changement de cloud à partir du **12 janvier 2027**), exigences de souveraineté.
Les fournisseurs offrent des outils gratuits pour *entrer* chez eux. Personne n'offre l'outil pour **vérifier la sortie**.

Canot fait cette répétition de départ, sans que vous quittiez votre fournisseur.

## Ce que ça fait

| Étape | Commande | Ce que vous obtenez |
|---|---|---|
| Radiographie | `canot scan` | tables, volumes, consommation sur 90 jours, colonnes à type propre à la plateforme, objets qui ne partent pas seuls (vues, routines, modèles ML…), durée et coût de sortie estimés |
| Exercice | `canot drill` | copie en Iceberg v2, puis comparaison table par table (schéma, lignes, min/max/sommes, empreinte de chaque ligne) et **rejeu de vos requêtes des deux côtés** avec tolérances écrites à l'avance |
| Preuve | `report.html`, `evidence.jsonl` | parité, conversions de types avec perte possible, débit mesuré, temps de reprise en fourchette, coût estimé. Journal chaîné par empreintes et signé (Ed25519) avec une clé créée chez vous |
| Contrôle | `canot verify-evidence` | toute modification du dossier après coup se voit |
| Nettoyage | `canot teardown` | supprime ce qui a été copié |

Un exemple sur données inventées : [`examples/distributeur-fictif/`](examples/distributeur-fictif/). Un exemple réel sur BigQuery (jeu public `thelook_ecommerce`) : [`examples/bigquery-thelook/`](examples/bigquery-thelook/), où Canot repère une requête qui diverge (`DATE_TRUNC` rend une `DATE` chez BigQuery, un horodatage ailleurs).
Ce que le client reçoit, offre par offre : [`docs/livrables.md`](docs/livrables.md).

## Essayer en 2 minutes, sans compte

```bash
pip install canot            # ou : uvx canot ...
canot demo-data maison.duckdb --retail
canot drill --from maison.duckdb --to ./lac --queries maison.queries.sql
canot verify-evidence canot-out
```

## Sources et cibles

| Sources | Cibles (stockage compatible S3) |
|---|---|
| Snowflake, BigQuery, Databricks, DuckDB (démo) | Scaleway, OVHcloud, Outscale, IONOS, STACKIT, AWS, ou un dossier local |

Détails, pays de l'UE et qualifications : [`docs/cibles-ue.md`](docs/cibles-ue.md).

## Sécurité

- Lecture seule sur la source, avec les droits minimaux (script SQL fourni : `canot snowflake-setup`).
- Identifiants uniquement par variables d'environnement. Aucune donnée ne passe par q4o.
- Versions signées et liste des composants (SBOM) pour votre équipe sécurité.

## État du projet

| | |
|---|---|
| Source DuckDB, cible locale Iceberg | testées de bout en bout (`pytest`) |
| Source BigQuery | testée sur un vrai projet Google (jeu public, 3,3 M de lignes copiées en 20 s, 7 tables identiques) : [`examples/bigquery-thelook/`](examples/bigquery-thelook/) |
| Sources Snowflake, Databricks | écrites, **pas encore testées sur un vrai compte** |
| Cibles S3 souveraines | écrites, à essayer |
| Module OpenTofu Scaleway | validé, jamais appliqué |

Canot n'est pas une certification, et ne dit rien de la conformité juridique de vos contrats.

## Contribuer, licence

Apache-2.0. Les cas de types qui piègent (`VARIANT`, `TIMESTAMP_TZ`, `NUMERIC`, tri des `NULL`) alimentent une suite de tests publique : une contribution utile est un cas qui fait diverger deux moteurs.
**Canot Vigie** (abonnement : dossiers signés planifiés, modèles réglementaires, support) et les missions de [q4o](https://q4o.com) sont proposés séparément.

<sub>Un nom provisoire : la marque n'a pas encore été vérifiée.</sub>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-dark.svg">
    <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-light.svg" alt="Canot : prouvez que vous pouvez partir" width="100%">
  </picture>
</p>

<p align="center">
  <b>Prouvez que vous pouvez partir.</b><br>
  Un exercice de sortie de votre plateforme de données, dans vos propres comptes.
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Français</b> ·
  <a href="https://canot.io"><b>canot.io</b></a> ·
  <a href="docs/commands.md">Documentation (en anglais)</a>
</p>

---

## ▶ Essayer

```bash
uvx canot demo
```

Sans compte ni données : la commande joue un exercice de sortie complet sur le jeu de référence TPC-H (copie en Apache Iceberg, comparaison, dossier de preuve signé) en quelques secondes.

<p align="center">
  <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/demo.svg" alt="uvx canot demo copie les 8 tables de TPC-H, compare tout et signale un écart" width="860">
</p>

Résultat : 7 tables sur 8 identiques, 3 requêtes sur 4 qui rendent le même résultat. L'écart vient d'une colonne horodatée à la nanoseconde : Iceberg v2 la ramène à la microseconde. Le genre d'écart qu'on veut découvrir **avant** de partir.

## ▶ Connecteurs

`canot connectors` les liste, avec ce que chacun sait faire (inventaire, export, preuve) et s'il a été validé.

| Connecteur | URL de la source | Statut |
|---|---|---|
| Snowflake | `snowflake://COMPTE/BASE` | disponible, testé : scan complet, copie, comparaison, rejeu des requêtes |
| BigQuery | `bigquery://PROJET/DATASET` | disponible, testé : scan complet, copie, comparaison, rejeu des requêtes |
| dbt | `dbt://CHEMIN_DU_PROJET` | disponible, testé : scan des fichiers du projet, rejeu des modèles (`canot replay-dbt`) |
| DuckDB | `fichier.duckdb` | disponible, testé : scan léger, copie, comparaison |
| Databricks | `databricks://HÔTE/CHEMIN_HTTP?catalog=…` | écrit, pas encore validé sur un compte réel |
| Microsoft Fabric / Azure Synapse, Amazon Redshift, Teradata, Oracle, SAP Datasphere, ClickHouse | | sur la feuille de route, pas encore de connecteur |

Lancer Canot sur une plateforme sans connecteur indique comment le demander. Un connecteur s'écrit et se valide avec vous, sur votre propre compte : [dites-nous ce que vous utilisez](https://canot.io/revue.html).

## 🧭 Pourquoi

|  |  |  |
|---|---|---|
| **Le déséquilibre** | **La pression** | **Le risque caché** |
| Snowflake et Databricks offrent des outils gratuits pour *entrer*. Personne n'offre l'outil qui vérifie la *sortie*. | DORA demande aux entités financières un plan de sortie **testé**. En France, le transfert de données lors d'un changement de cloud est déjà à 0 € ; le **12 janvier 2027**, tous les frais de changement disparaissent (Data Act). | Le travail, lui, reste : durée, écarts et ce qui ne part pas tel quel sont inconnus tant que personne n'a exécuté la sortie. |

Canot fait la répétition de départ. Vous restez chez votre fournisseur.

## ▶ Sur vos données

```bash
uvx canot scan "snowflake://MON_COMPTE/MA_BASE?user=MON_UTILISATEUR"    # radiographie en lecture seule
uvx canot drill "snowflake://MON_COMPTE/MA_BASE?user=MON_UTILISATEUR" ./iceberg   # exercice de sortie complet, vers un dossier local
```

Sortie vers votre propre infrastructure (dossier, NAS, stockage S3 maison), commandes, droits et accès pour un agent (MCP) : voir la [documentation](docs/commands.md) (en anglais). `uvx canot connectors` liste les plateformes prises en charge. Canot n'est **pas** une certification : il produit un relevé daté de mesures sur le périmètre exécuté. Le site, [canot.io](https://canot.io), explique le reste.

<sub>Apache-2.0 · Aucun pisteur, aucune télémétrie · [Politique de sécurité](SECURITY.md) · [Q4O](https://q4o.com)</sub>

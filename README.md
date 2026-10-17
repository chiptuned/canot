<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-dark.svg">
    <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/banner-light.svg" alt="Canot : prouvez que vous pouvez partir" width="100%">
  </picture>
</p>

<p align="center">
  <b>Prouvez que vous pouvez partir.</b> · <i>Prove you can leave.</i><br>
  Un exercice de sortie de votre plateforme de données, dans vos comptes.
</p>

<p align="center">
  <img alt="licence Apache-2.0" src="https://img.shields.io/badge/licence-Apache--2.0-1F3A5F?style=flat-square">
  <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-1F3A5F?style=flat-square">
  <img alt="aucune télémétrie" src="https://img.shields.io/badge/télémétrie-aucune-2F7A4B?style=flat-square">
  <img alt="Snowflake, BigQuery" src="https://img.shields.io/badge/Snowflake%20·%20BigQuery-1F3A5F?style=flat-square">
</p>

<p align="center">
  <a href="https://canot.io"><b>canot.io</b></a> ·
  <a href="docs/commandes.md">Documentation</a> ·
  <a href="docs/ecarts.md">Écarts mesurés</a> ·
  <a href="README.en.md">English</a> ·
  <a href="#-essayer">Essayer</a> ·
  <a href="#-pourquoi">Pourquoi</a>
</p>

---

## ▶ Essayer

Une commande : la source, la destination. Un dossier local suffit.

```bash
uvx canot drill "bigquery://MON-PROJET/mon_dataset?location=EU" ./lac
```

Canot copie les tables en Apache Iceberg, compare tout, rejoue vos requêtes les plus coûteuses et écrit un dossier de preuve signé. Pas de données sous la main : `uvx canot demo` (trente secondes, sans compte).

<p align="center">
  <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/demo.svg" alt="canot copie des tables BigQuery, compare tout, et signale une requête qui diverge" width="860">
</p>

Sur un jeu public BigQuery : 7 tables au contenu identique, 3 requêtes sur 4 qui rendent le même résultat. La 4<sup>e</sup> rend une date chez BigQuery et une date avec heure ailleurs. C'est le genre d'écart qu'on veut découvrir **avant** de partir.

## 🧭 Pourquoi

|  |  |  |
|---|---|---|
| **Le déséquilibre** | **La pression** | **Le risque caché** |
| Snowflake et Databricks offrent des outils gratuits pour *entrer*. Personne n'offre l'outil qui vérifie la *sortie*. | DORA demande un plan de sortie **testé** aux entités financières. En France, le transfert de données lors d'un changement de cloud est déjà à 0 € ; le **12 janvier 2027**, tous les frais de changement disparaissent (Data Act). | Le travail, lui, reste : durée, écarts et ce qui ne part pas tel quel sont inconnus tant que personne n'a exécuté la sortie. |

Canot fait la répétition de départ. Vous restez chez votre fournisseur.

## ▶ À vous

```bash
uvx canot scan "bigquery://MON-PROJET"                      # radiographie en lecture seule
uvx canot drill "bigquery://MON-PROJET/mon_dataset" ./lac   # exercice de sortie complet, en local
```

Sortie sur votre infrastructure (dossier, NAS, stockage S3 maison), commandes, droits et accès pour un agent (MCP) : la [documentation](docs/commandes.md). Le site, [canot.io](https://canot.io), explique le reste.

<sub>Apache-2.0 · Aucun pisteur, aucune télémétrie · [q4o](https://q4o.com)</sub>

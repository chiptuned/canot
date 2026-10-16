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
  <img alt="Snowflake, BigQuery, Databricks" src="https://img.shields.io/badge/Snowflake%20·%20BigQuery%20·%20Databricks-1F3A5F?style=flat-square">
</p>

<p align="center">
  <a href="https://canot.io"><b>canot.io</b></a> ·
  <a href="#-essayer">Essayer</a> ·
  <a href="#-pourquoi">Pourquoi</a>
</p>

---

## ▶ Essayer

Trente secondes, sans compte ni données. Un distributeur fictif, un exercice de sortie complet, et le dossier de preuve s'ouvre dans votre navigateur.

```bash
uvx canot demo
```

<p align="center">
  <img src="https://raw.githubusercontent.com/chiptuned/canot/main/docs/assets/demo.svg" alt="canot copie des tables BigQuery, compare tout, et signale une requête qui diverge" width="860">
</p>

Sur un jeu public BigQuery : 7 tables identiques ligne par ligne, 3 requêtes sur 4 qui rendent le même résultat. La 4<sup>e</sup> rend une date chez BigQuery et une date avec heure ailleurs. C'est le genre d'écart qu'on veut découvrir **avant** de partir.

## 🧭 Pourquoi

|  |  |  |
|---|---|---|
| **Le déséquilibre** | **La pression** | **Le risque caché** |
| Snowflake et Databricks offrent des outils gratuits pour *entrer*. Personne n'offre l'outil qui vérifie la *sortie*. | DORA demande un plan de sortie **testé** aux entités financières. Le Data Act supprime les frais de changement de cloud le **12 janvier 2027**. | Un plan de sortie sur papier n'a jamais été exécuté : sa durée, son coût et ses écarts sont inconnus. |

Canot fait la répétition de départ. Vous restez chez votre fournisseur.

## ▶ À vous

```bash
uvx canot demo
```

Puis sur vos données, en lecture seule : `canot scan --from "bigquery://MON-PROJET/mon_dataset?location=EU"`. Le site, [canot.io](https://canot.io), explique le reste.

<sub>Apache-2.0 · Aucun pisteur, aucune télémétrie · [q4o](https://q4o.com)</sub>

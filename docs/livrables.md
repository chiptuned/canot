# Livrables : ce que le client reçoit, offre par offre

Chaque livrable a un format, un critère de recette (comment le client dit « c'est bon ») et ce qu'il doit nous fournir.
Les jours et les prix sont des estimations à valider avec les premiers clients. L'exemple chiffré est dans
[`examples/distributeur-fictif/`](../examples/distributeur-fictif/) (données inventées d'un distributeur fictif).

## 1. Diagnostic technique de réversibilité (22 k€ HT, 3 semaines, environ 15 jours de travail)

Question à laquelle il répond : « si nous voulions quitter cette plateforme de données, que faudrait-il faire, combien de temps, combien ça coûterait, et pouvons-nous le prouver ? »

| # | Livrable | Format | Critère de recette |
|---|---|---|---|
| D1 | **Radiographie** : tables, volumes, colonnes à type propre à la plateforme, consommation sur 90 jours, requêtes les plus coûteuses | `scan.json` + synthèse d'une page | le référent data confirme que l'inventaire correspond à son périmètre |
| D2 | **Exercice de sortie** sur un périmètre convenu (un domaine, 5 à 20 tables) | `report.html` + `report.json` | parité complète, ou chaque écart expliqué et classé (erreur de copie, règle de conversion, différence de moteur) |
| D3 | **Dossier de preuve signé** | `evidence.jsonl`, `.sig`, `.pub` | `canot verify-evidence` passe ; le client le versionne dans son propre archivage |
| D4 | **Scénarios de coût de sortie** (trois : minimal, nominal, prudent) | tableur + 2 pages | les hypothèses sont nommées, chaque ligne a sa source (mesure, tarif du client, ou « est. ») |
| D5 | **Registre de ce qui ne part pas tel quel** : modèles ML, fonctions et procédures, requêtes planifiées, vues autorisées, politiques de sécurité par ligne et par colonne, vues matérialisées, réservations de capacité | tableur : objet, effort (S/M/L), remplaçant proposé | le référent confirme qu'aucun objet important n'est oublié |
| D6 | **Relevé factuel du contrat** : mode de facturation, dates de fin d'engagement, préavis, support souscrit, localisation des données | 1 page de faits, sans qualification juridique | validé par les achats ; l'analyse juridique reste celle de l'avocat du client |
| D7 | **Marche à suivre de sortie** (runbook) : étapes, ordre, durée, rôles, points de contrôle | document de 5 à 10 pages | relu par l'équipe qui l'exécuterait |
| D8 | **Dossier de renouvellement** : coût de sortie mesuré, comparaison des scénarios, ce que le client peut demander à son fournisseur | 2 pages pour la direction | présenté et pris en main par le DSI ou les achats |
| D9 | **Restitution** | atelier de 2 h + support | questions tracées, actions décidées |

À fournir par le client : accès en lecture seule (rôles listés dans la doc de chaque source), un référent data (environ une demi-journée), un administrateur (environ une heure), la liste des 10 à 20 rapports ou requêtes qui comptent, le tarif réellement payé, la date de renouvellement.
Hors périmètre : réécriture des applications, conseil juridique, déplacement réel des données de production, choix de la plateforme cible.

### Postes à chiffrer pour une sortie de BigQuery (D4)

Aucun montant n'est fourni ici : les tarifs Google changent et dépendent du contrat. Chaque poste se remplit avec le tarif du client.

1. Lecture ou export des données (API Storage Read par Tio lu, ou export vers Cloud Storage).
2. Sortie réseau depuis Google Cloud. Google propose un programme de sortie qui peut la créditer sous conditions (liste de produits éligibles, résiliation du contrat, fenêtre de migration) : voir cloud.google.com/exit-cloud, à confirmer produit par produit.
3. Stockage chez le nouvel hébergeur, par mois.
4. Période de double exploitation (BigQuery et la cible en même temps).
5. Calcul sur la cible.
6. Temps d'ingénierie : traduction des requêtes et routines (dialecte BigQuery), validation, reconnexion des outils de reporting.

## 2. Test de sortie DORA (25 à 60 k€, puis re-test 10 à 30 k€/an)

Pour une entité financière. Question : « notre plan de sortie pour ce prestataire est-il réaliste, et l'a-t-on testé ? »

| # | Livrable | Critère de recette |
|---|---|---|
| T1 | Scénarios de test (sortie planifiée, défaillance du prestataire, perte de service) rattachés à l'art. 10 du règlement délégué 2024/1773 | validés par le responsable des risques |
| T2 | Exécution sur un périmètre critique, avec les mesures (durée, parité, coût) | dossier de preuve signé (D3) |
| T3 | Fiche de test : date, périmètre, résultat, écarts, actions | acceptée par le contrôle interne |
| T4 | Éléments de registre : existence d'un plan, prestataires alternatifs identifiés | relus par la conformité |
| T5 | Synthèse d'une page pour le comité | présentée |

Ce n'est pas une attestation de conformité DORA. Un re-test récurrent peut faire de q4o un prestataire informatique de l'entité (clauses de l'art. 30) : contractualiser en missions ponctuelles d'abord.

## 3. Canot Vigie (abonnement, 15 k€/an par compte source)

Refaire l'exercice chaque trimestre. Livrables : dossier signé par trimestre, comparaison avec le précédent (dérive), alerte quand un nouvel objet non portable apparaît, modèles réglementaires à jour, support avec délai de réponse écrit. Critère de recette : le trimestre se déroule sans intervention de q4o autre que la lecture du rapport.

## 4. Pilote de parité SAS (45 k€ fixe, 6 semaines)

| # | Livrable | Critère de recette |
|---|---|---|
| S1 | Carte des dépendances et du code mort d'une chaîne | validée par l'équipe propriétaire |
| S2 | 40 programmes convertis et prouvés identiques sur les données du client | rapport de parité par programme, tolérances signées avant |
| S3 | Devis ferme de la suite, par domaine | accepté ou refusé par écrit |
| S4 | Note d'appui au renouvellement de licence | remise |

Condition : travail sur la licence SAS du client, jamais sur une version Learning ou Academic.

## 5. Kit export pour éditeur SaaS (10 à 40 k€, 3 à 6 semaines)

Une fonction d'export documentée (OpenAPI), le contenu du registre exigé par l'art. 26 du Data Act, un jeu de tests d'export, la marche à suivre. Recette : un client test exporte ses données seul en suivant la documentation.

## 6. Ce que nous ne livrons jamais

Un avis juridique, une certification, une garantie de gratuité du départ, un accès permanent à l'environnement du client.

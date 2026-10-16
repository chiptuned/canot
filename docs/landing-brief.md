# Brief : landing page de Canot (pour l'agent de design)

Ce document donne la matière, pas la mise en page. Le design est libre, dans les limites de la dernière section.

## 1. Le produit en une phrase
Canot fait un **exercice de sortie** de Snowflake, BigQuery ou Databricks : il copie les données vers un format ouvert (Apache Iceberg) sur un cloud que le client contrôle, compare tout ligne par ligne, rejoue ses requêtes clés des deux côtés, et signe un **dossier de preuve**. Une commande, dans les comptes du client, aucune donnée ne passe par nous.

## 2. À qui parle la page (trois lecteurs, dans cet ordre)
1. **Le responsable des risques ou de la conformité** d'une banque, assurance ou société de gestion : il doit prouver que le plan de sortie de son prestataire cloud est réaliste et testé (DORA).
2. **Le DSI ou directeur data** d'une entreprise de taille intermédiaire qui renouvelle bientôt son contrat de plateforme de données : il veut un coût de sortie mesuré pour négocier.
3. **L'ingénieur data** qui lit le dépôt GitHub : il veut voir la commande, le code, le format du rapport, et savoir ce qui est testé ou non.

Un même visiteur doit pouvoir aller du « pourquoi » à `uvx canot scan` en moins d'une minute.

## 3. Le message
- **Titre candidat :** « Prouvez que vous pouvez partir. » (EN : *Prove you can leave.*)
- **Sous-titre candidat :** « Une répétition de départ de Snowflake, BigQuery ou Databricks, dans vos propres comptes, avec un dossier de preuve signé. »
- **Accroche d'actualité (bandeau, pas titre) :** « Le 12 janvier 2027, les frais de changement de cloud disparaissent en Europe (Data Act). Le travail de sortie, lui, reste. »
- **Alternatives de titre à tester :** « Votre plan de sortie, exécuté et mesuré. » · « Combien coûterait vraiment votre sortie ? » · « Ce que vos contrats cloud ne vous disent pas : si vous pouvez partir. »

## 4. Structure proposée
1. **Hero** : titre, sous-titre, la commande copiable, deux boutons (« Lancer un scan gratuit » vers GitHub, « Parler à q4o »). Visuel : voir §5.
2. **Le problème en trois lignes** : les fournisseurs offrent des outils gratuits pour entrer, personne n'offre celui qui vérifie la sortie ; DORA et le Data Act demandent de pouvoir partir ; un plan de sortie sur papier n'a jamais été exécuté.
3. **Comment ça marche** : cinq étapes (installer, copier, rejouer, comparer, signer) sur un schéma où le **périmètre du client** est une frontière dessinée, avec « q4o n'envoie rien » à l'extérieur.
4. **Le dossier de preuve** : une capture réelle du rapport (voir `examples/bigquery-thelook/report.html`), avec trois annotations : parité par table, requête qui diverge, journal signé.
5. **Preuve par l'exemple** : « 3,3 millions de lignes, 7 tables identiques, 20 secondes. Et une requête qui diverge : `DATE_TRUNC` rend une `DATE` chez BigQuery, un horodatage ailleurs. C'est exactement ce que vous voulez savoir avant de partir. »
6. **Pour qui, et pourquoi maintenant** : trois cartes (risques / DSI / ingénieur) avec une phrase chacune, et une frise courte : 17 janv. 2025 DORA, 12 sept. 2025 Data Act, **12 janv. 2027** fin des frais de changement.
7. **Cibles souveraines** : Scaleway, OVHcloud, Outscale, IONOS, STACKIT, AWS ; bandeau « France, Allemagne, Pays-Bas, Italie, Espagne ». Ne pas écrire « qualifié SecNumCloud » sans la mention « selon l'hébergeur choisi ».
8. **Offres** : outil gratuit (Apache-2.0) · diagnostic de réversibilité 22 k€ HT, 3 semaines · test de sortie DORA à partir de 25 k€ · abonnement Canot Vigie 15 k€/an. Une ligne de livrables par offre (voir `docs/livrables.md`).
9. **Honnêteté** : un tableau d'état du projet (testé, écrit mais non testé). C'est un argument de confiance, pas un défaut à cacher.
10. **FAQ / objections** : « Si l'outil est gratuit, pourquoi payer ? » · « Que voyez-vous de nos données ? (rien) » · « Est-ce une certification ? (non) » · « Pouvez-vous lire nos contrats ? (non, c'est un travail d'avocat, nous travaillons avec un cabinet) » · « Que faire si on ne veut pas partir ? (l'exercice ne vous fait pas partir) ».
11. **Appel final** : la commande, le lien GitHub, un formulaire court (nom, société, plateforme actuelle, date de renouvellement).

## 5. Idées visuelles
- **Métaphore du canot de sauvetage**, discrète : un tracé de canot ou une bouée, jamais un clip-art. On répète un départ, on ne le subit pas. Le mot « exercice » (un exercice d'évacuation) porte l'idée.
- **Le périmètre** : le schéma central est une frontière en pointillés autour des comptes du client, avec les flèches de la source vers la cible à l'intérieur. C'est l'image qui rassure un RSSI.
- **Le rapport comme héros** : le vrai rapport, lisible, plutôt qu'un mockup abstrait. Le dossier de preuve est le produit.
- **Le terminal** : la commande dans un bloc sombre copiable, avec une vraie sortie (les lignes `OK`, `[match]`, `[mismatch]`).
- **Ton graphique** : sobre et technique, plutôt institutionnel que « startup ». Palette de départ : marine profond, un orange de sécurité (couleur d'un gilet de sauvetage) comme seul accent, des gris bleutés. Chiffres en police à chasse fixe.
- **À éviter** : dégradés violet-bleu, illustrations de nuage, cadenas partout, captures de dashboards inventés, statistiques sans source.

## 6. Ton et vocabulaire
- Français d'abord, anglais ensuite (page jumelle). Tutoiement exclu, vouvoiement sobre.
- Phrases courtes, concrètes, chiffrées. Pas de « révolutionnaire », « seamless », « 100 % sécurisé ».
- Mots à utiliser : exercice de sortie, dossier de preuve, parité, dans vos comptes, réversibilité.
- Mots à ne jamais utiliser : « conforme DORA », « certifié », « garanti », « gratuit » à propos du départ (l'outil est gratuit, partir ne l'est pas), « clone ».

## 7. Contraintes à respecter (juridiques et de confiance)
- Dire « rien ne quitte vos comptes », jamais « aucune donnée ne sort » (les données sont copiées, chez le client).
- Ne citer aucun client. Aucun logo de tiers hors des hébergeurs en liste de compatibilité, sous forme de texte.
- Chaque chiffre de marché porte sa source en note (voir `docs/livrables.md` et le briefing). Les prix sont des « à partir de ».
- Le nom **Canot** est provisoire (marque non vérifiée) : prévoir de le changer facilement, ne pas le dessiner dans un logo figé.
- Aucun formulaire ne collecte de données personnelles au-delà du nécessaire ; pas de pisteur tiers (cohérent avec « aucune télémétrie »).

## 8. Matériaux fournis
- Dépôt : `~/repos/canot` (GitHub : chiptuned/canot).
- `README.md` : version courte de la page. `docs/livrables.md` : ce que le client reçoit. `docs/cibles-ue.md` : hébergeurs et pays.
- `examples/bigquery-thelook/report.html` et `examples/distributeur-fictif/report.html` : deux vrais rapports.
- Briefing interne (contexte, vocabulaire, argumentaire) : `docs/briefing.html`.

## 9. Mesure de réussite
Le nombre de visiteurs qui lancent `canot scan` (clic sur la commande copiée) et le nombre de demandes de rendez-vous qualifiées (plateforme actuelle + date de renouvellement renseignées). Pas les pages vues.

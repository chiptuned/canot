# Sélecteur de plateforme source (variante 3b)

Le nom de la plateforme tourne dans l'accroche et dans la commande, qui restent liées. Les noms du paragraphe servent aussi de sélecteur. Snowflake est la plateforme par défaut.

`index.html` est une démo autonome : ouvrez-la dans un navigateur.

## Fichiers

- `selecteur-source.css` : styles du tambour et des liens.
- `selecteur-source.js` : comportement, sans dépendance (~5 Ko).
- `index.html` : démo.

## Intégration sur canot.io

1. Ajoutez `selecteur-source.css` dans le `<head>` et `<script src="selecteur-source.js" defer></script>` avant `</body>`.
2. Ajoutez `data-cs-root` sur l'élément qui contient l'accroche, la commande et le paragraphe (le bloc hero).
3. Annotez les trois zones existantes :

```html
<!-- Accroche -->
Enfermé chez <span class="cs-drum--title" data-cs-drum="label">Snowflake</span>&nbsp;? Prouvez le contraire.

<!-- Commande -->
uvx canot drill "<span class="cs-drum--code" data-cs-drum="scheme">snowflake</span><span data-cs-rest>://MY_ACCOUNT/MY_DATABASE?user=MY_USER</span>" ./iceberg
<button type="button" data-cs-copy>copier</button>

<!-- Paragraphe -->
Votre plateforme en entrée (<a href="?source=snowflake" data-cs-pick="snowflake">Snowflake</a>,
<a href="?source=bigquery" data-cs-pick="bigquery">BigQuery</a> ou
<a href="?source=databricks" data-cs-pick="databricks">Databricks</a>), un dossier en sortie…
```

Sans JavaScript, la page affiche la commande Snowflake complète, telle qu'elle est écrite dans le HTML.

## Comportement

- Pour changer de plateforme : molette, flèches ↑↓ (le mot a le focus clavier), clic sur une voisine au survol, ou clic sur un nom du paragraphe.
- Au chargement, un seul tour de démonstration après 1,4 s. Il s'arrête dès que l'utilisateur survole le tambour, le sélectionne au clavier ou clique, et ne revient plus. Il est désactivé si `prefers-reduced-motion` est actif ou si l'URL contient `?source=`.
- En bout de liste, la molette fait défiler la page normalement.
- Le bouton « copier » copie la commande complète de la plateforme affichée. Pour une sélection à la souris, seul le mot actif est sélectionnable : les voisines cachées ne sont pas copiées.
- `?source=databricks` (ou `bigquery`) dans l'URL présélectionne la plateforme, pratique pour des liens ciblés.

## Ajouter une plateforme

Ajoutez une entrée dans `PLATFORMS`, en haut de `selecteur-source.js`, et un lien dans le paragraphe :

```js
{ id: 'redshift', label: 'Redshift', rest: '://MY_HOST:5439/MY_DATABASE?user=MY_USER' }
```

Gardez la commande la plus longue en dernière position : les voisines, affichées sous le mot, ne recouvrent alors jamais la deuxième ligne de la commande.

## Couleurs

Les variables CSS sont en haut de `selecteur-source.css` (`--cs-accent`, `--cs-muted`, `--cs-chip`, `--cs-line`). Remplacez-les par celles du site.

"""Which elements of a Canot report can support your own work on GDPR, DORA, NIS2, the Data Act, SecNumCloud and the EBA outsourcing guidelines.

Canot does not assess compliance and certifies nothing. This table says where an element of the report can serve as evidence, and what it
does not cover. References are to the texts as of 2026-10-19: check the current versions before relying on them."""
from __future__ import annotations

import html

AS_OF = "2026-10-19"

# (framework, requirement, element of the report, limit, kinds in which it appears)
ROWS = (
    ("DORA (Regulation (EU) 2022/2554)",
     "Art. 28(8): exit plans for ICT services supporting the functions DORA targets are documented and tested.",
     "The exit exercise itself: its date, its scope, what was copied and compared, what differs, in a signed log.",
     "One exercise on one scope. DORA also expects periodic review and scenarios you define yourself.", ("drill",)),
    ("DORA (Regulation (EU) 2022/2554)",
     "Art. 28(8) and 30(3)(f): a realistic transition, with an adequate period.",
     "Measured throughput and a recovery time range from the exercise; a copy duration range (estimated) in the scan.",
     "Excludes human validation and the cutover of your applications.", ("drill", "scan")),
    ("DORA (Regulation (EU) 2022/2554)",
     "Art. 28: knowing what depends on the ICT provider.",
     "What does not move as is: views, routines, tasks, policies, external dependencies, with the evidence behind each finding.",
     "An inventory of the platform, not of your contracts or of the applications connected to it.", ("drill", "scan")),
    ("Data Act (Regulation (EU) 2023/2854)",
     "Arts. 23 to 31 (switching between data processing services); Art. 30: export in a structured, commonly used, machine-readable format.",
     "A copy in Apache Iceberg, an open format, that can be reread without Canot (the reread.sql file next to the report).",
     "Whether your provider meets its own obligations is a matter for your contract.", ("drill",)),
    ("Data Act (Regulation (EU) 2023/2854)",
     "Art. 29: switching charges are withdrawn from 12 January 2027.",
     "The effort that remains yours: read cost, copy duration and rework estimates, each marked measured, computed or estimated.",
     "Says nothing about charges your provider may still apply before that date. Prices are dated catalogue figures, not your contract.", ("drill", "scan")),
    ("GDPR (Regulation (EU) 2016/679)",
     "Art. 5(2) accountability; Art. 28(3)(g): return or deletion of personal data at the end of the service.",
     "A signed record of what was copied, when and to where. Any change to the log shows.",
     "Does not cover deletion at the source. The signature attests integrity, not the author or the date.", ("drill",)),
    ("GDPR (Regulation (EU) 2016/679)",
     "Chapter V (transfers) and Art. 32 (security of processing).",
     "Where the copy is hosted, and which security objects do not move with it (masking and row policies, tags).",
     "The hosting location is the destination you chose. The legal assessment of a transfer is yours.", ("drill", "scan")),
    ("GDPR (Regulation (EU) 2016/679)",
     "Art. 5(1)(c): data minimisation.",
     "The scan reads metadata and execution history, not table contents. SQL text is masked and identities are pseudonymised.",
     "Applies to the scan only: an exit exercise (drill) reads the data it copies.", ("scan",)),
    ("SecNumCloud (ANSSI)",
     "The qualification concerns the provider's offer, and its referential includes reversibility requirements.",
     "Where the copy lands (provider, region) so you can check that offer against the ANSSI list of qualified offers, and an open-format copy.",
     "Canot does not assess or certify an offer. Qualification depends on the precise offer and is decided by ANSSI.", ("drill",)),
    ("NIS2 (Directive (EU) 2022/2555)",
     "Art. 21(2)(c): business continuity, including backup management and recovery.",
     "Evidence that the data can be restored in an open format elsewhere: a copy compared with the source, and a recovery time range.",
     "Not a backup or disaster-recovery plan. One exercise on one scope.", ("drill",)),
    ("NIS2 (Directive (EU) 2022/2555)",
     "Art. 21(2)(d): security of the supply chain, including your relationship with each direct service provider.",
     "What ties you to this provider: external dependencies and objects that do not move as is, with the evidence behind each finding.",
     "Does not assess the provider's security measures.", ("drill", "scan")),
    ("EBA Guidelines on outsourcing arrangements (EBA/GL/2019/02)",
     "Exit strategies: documented, realistic, sufficiently tested and reviewed periodically for the outsourcing the guidelines target.",
     "The exit exercise (drill) and the inventory of what does not move (scan), as test evidence in your exit file.",
     "One exercise. Alternative providers, the business side and the periodic review are yours. Check which text applies to you alongside DORA.", ("drill", "scan")),
)

# French text of each row, same order as ROWS: (framework, requirement, element of the report, limit). Legal names as published.
ROWS_FR = (
    ("DORA (règlement (UE) 2022/2554)",
     "Art. 28, § 8 : les plans de sortie des services TIC qui soutiennent les fonctions visées par DORA sont documentés et testés.",
     "L'exercice de sortie lui-même : sa date, son périmètre, ce qui a été copié et comparé, ce qui diffère, dans un journal signé.",
     "Un exercice sur un périmètre. DORA attend aussi un réexamen périodique et des scénarios que vous définissez vous-même."),
    ("DORA (règlement (UE) 2022/2554)",
     "Art. 28, § 8, et art. 30, § 3, point f) : une transition réaliste, avec une période adéquate.",
     "Le débit mesuré et une fourchette de temps de reprise issus de l'exercice ; une fourchette de durée de copie (estimée) dans le scan.",
     "Hors validation humaine et bascule de vos applications."),
    ("DORA (règlement (UE) 2022/2554)",
     "Art. 28 : savoir ce qui dépend du prestataire TIC.",
     "Ce qui ne se déplace pas tel quel : vues, routines, tâches, politiques, dépendances externes, avec les éléments à l'appui de chaque constat.",
     "Un inventaire de la plateforme, pas de vos contrats ni des applications qui y sont connectées."),
    ("Data Act (règlement (UE) 2023/2854)",
     "Art. 23 à 31 (changement de service de traitement de données) ; art. 30 : export dans un format structuré, couramment utilisé et lisible par machine.",
     "Une copie au format Apache Iceberg, un format ouvert, relisible sans Canot (le fichier reread.sql à côté du rapport).",
     "Le respect par votre prestataire de ses propres obligations relève de votre contrat."),
    ("Data Act (règlement (UE) 2023/2854)",
     "Art. 29 : les frais de changement sont supprimés à partir du 12 janvier 2027.",
     "L'effort qui reste à votre charge : coût de lecture, durée de copie et estimations de reprise, chacun marqué mesuré, calculé ou estimé.",
     "Ne dit rien des frais que votre prestataire peut encore appliquer avant cette date. Les prix sont des tarifs catalogue datés, pas votre contrat."),
    ("RGPD (règlement (UE) 2016/679)",
     "Art. 5, § 2 : responsabilité ; art. 28, § 3, point g) : restitution ou suppression des données personnelles à la fin du service.",
     "Un relevé signé de ce qui a été copié, quand et vers où. Toute modification du journal se voit.",
     "Ne couvre pas la suppression à la source. La signature atteste l'intégrité, pas l'auteur ni la date."),
    ("RGPD (règlement (UE) 2016/679)",
     "Chapitre V (transferts) et art. 32 (sécurité du traitement).",
     "L'endroit où la copie est hébergée, et les objets de sécurité qui ne la suivent pas (politiques de masquage et d'accès aux lignes, tags).",
     "Le lieu d'hébergement est la destination que vous avez choisie. L'appréciation juridique d'un transfert vous revient."),
    ("RGPD (règlement (UE) 2016/679)",
     "Art. 5, § 1, point c) : minimisation des données.",
     "Le scan lit les métadonnées et l'historique d'exécution, pas le contenu des tables. Le texte SQL est masqué et les identités sont pseudonymisées.",
     "Vaut pour le scan seulement : un exercice de sortie (drill) lit les données qu'il copie."),
    ("SecNumCloud (ANSSI)",
     "La qualification porte sur l'offre du prestataire, et son référentiel comprend des exigences de réversibilité.",
     "L'endroit où arrive la copie (prestataire, région), pour vérifier cette offre dans la liste ANSSI des offres qualifiées, et une copie au format ouvert.",
     "Canot n'évalue ni ne certifie aucune offre. La qualification dépend de l'offre précise et relève de l'ANSSI."),
    ("NIS2 (directive (UE) 2022/2555)",
     "Art. 21, § 2, point c) : continuité des activités, y compris la gestion des sauvegardes et la reprise.",
     "Des éléments montrant que les données peuvent être restaurées ailleurs dans un format ouvert : une copie comparée à la source, et une fourchette de temps de reprise.",
     "Ni un plan de sauvegarde ni un plan de reprise après sinistre. Un exercice sur un périmètre."),
    ("NIS2 (directive (UE) 2022/2555)",
     "Art. 21, § 2, point d) : sécurité de la chaîne d'approvisionnement, y compris la relation avec chaque prestataire direct.",
     "Ce qui vous lie à ce prestataire : dépendances externes et objets qui ne se déplacent pas tels quels, avec les éléments à l'appui de chaque constat.",
     "N'évalue pas les mesures de sécurité du prestataire."),
    ("Orientations de l'ABE sur l'externalisation (EBA/GL/2019/02)",
     "Stratégies de sortie : documentées, réalistes, suffisamment testées et réexaminées périodiquement, pour les externalisations visées par les orientations.",
     "L'exercice de sortie (drill) et l'inventaire de ce qui ne se déplace pas (scan), comme éléments de test dans votre dossier de sortie.",
     "Un exercice. Les prestataires alternatifs, le volet métier et le réexamen périodique vous reviennent. Vérifiez quel texte s'applique à vous en plus de DORA."),
)
assert len(ROWS_FR) == len(ROWS)

TEXT = {
    "en": {
        "heading": "Using this report for your regulatory work (GDPR, DORA, NIS2, Data Act, SecNumCloud, EBA)",
        "intro": ("These elements can support your own work on GDPR, DORA, NIS2, the Data Act, SecNumCloud or the EBA outsourcing guidelines. "
                  "They are evidence, not a finding: this report is not a certification, it is not legal advice, and it does not say whether "
                  "your contracts or your organization meet any regulation."),
        "cols": ("Framework", "What it asks, in short", "What this report gives you", "What it does not cover"),
        "footer": f"References as of {AS_OF}. Check the current texts: they evolve, and their application depends on your situation.",
    },
    "fr": {
        "heading": "Utiliser ce rapport pour vos travaux réglementaires (RGPD, DORA, NIS2, Data Act, SecNumCloud, ABE)",
        "intro": ("Ces éléments peuvent appuyer vos propres travaux sur le RGPD, DORA, NIS2, le Data Act, SecNumCloud ou les orientations de "
                  "l'ABE sur l'externalisation. Ce sont des éléments de preuve, pas une conclusion : ce rapport n'est pas une certification, "
                  "ce n'est pas un conseil juridique, et il ne dit pas si vos contrats ou votre organisation satisfont à une réglementation."),
        "cols": ("Cadre", "Ce qu'il demande, en bref", "Ce que ce rapport vous apporte", "Ce qu'il ne couvre pas"),
        "footer": (f"Références arrêtées au {AS_OF}. Vérifiez les textes en vigueur : ils évoluent, et leur application dépend de votre "
                   "situation."),
    },
}
INTRO = TEXT["en"]["intro"]


def rows_for(kind: str):
    return [r for r in ROWS if kind in r[4]]


def _rows(kind: str, lang: str):
    """(framework, requirement, element, limit) in the language asked, for the rows of this kind of report."""
    texts = ROWS_FR if lang == "fr" else [r[:4] for r in ROWS]
    return [t for r, t in zip(ROWS, texts) if kind in r[4]]


def section(kind: str, lang: str = "en", *, tag: str = "section", anchor: str | None = None) -> str:
    """HTML block for a report ('drill' or 'scan'), in English or French: self-contained, no script, no link.
    The id is `anchor` when given, else "regulatory" (English) or "regulatory-fr" (French), so the two languages of one page never share an id."""
    e = html.escape
    lang = "fr" if lang == "fr" else "en"
    t = TEXT[lang]
    anchor = anchor or ("regulatory" if lang == "en" else "regulatory-fr")
    body = "".join(f"<tr><td>{e(fw)}</td><td>{e(req)}</td><td>{e(got)}</td><td>{e(lim)}</td></tr>" for fw, req, got, lim in _rows(kind, lang))
    head = "".join(f"<th>{e(c)}</th>" for c in t["cols"])
    return (f'<{tag} id="{e(anchor)}"><h2>{e(t["heading"])}</h2><p>{e(t["intro"])}</p>'
            f"<table><tr>{head}</tr>{body}</table>"
            f'<p class="small">{e(t["footer"])}</p></{tag}>')

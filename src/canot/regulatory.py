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

INTRO = ("These elements can support your own work on GDPR, DORA, NIS2, the Data Act, SecNumCloud or the EBA outsourcing guidelines. They are evidence, not a finding: "
         "this report is not a certification, it is not legal advice, and it does not say whether your contracts or your organization meet any regulation.")


def rows_for(kind: str):
    return [r for r in ROWS if kind in r[4]]


def section(kind: str, lang: str = "en", *, tag: str = "section") -> str:
    """HTML block for a report ('drill' or 'scan'): self-contained, no script, no link."""
    e = html.escape
    body = "".join(f"<tr><td>{e(fw)}</td><td>{e(req)}</td><td>{e(got)}</td><td>{e(lim)}</td></tr>" for fw, req, got, lim, _ in rows_for(kind))
    return (f'<{tag} id="regulatory"><h2>Using this report for your regulatory work (GDPR, DORA, NIS2, Data Act, SecNumCloud, EBA)</h2><p>{e(INTRO)}</p>'
            f"<table><tr><th>Framework</th><th>What it asks, in short</th><th>What this report gives you</th><th>What it does not cover</th></tr>{body}</table>"
            f'<p class="small">References as of {AS_OF}. Check the current texts: they evolve, and their application depends on your situation.</p></{tag}>')

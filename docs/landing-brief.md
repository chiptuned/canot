# Brief: the Canot landing page (for the design agent)

This document provides the material, not the layout. The design is free, within the limits of the last sections.

## 1. The product in one sentence
Canot runs an **exit exercise** from Snowflake, BigQuery or Databricks: it copies the data to an open format (Apache Iceberg) on a cloud the client controls, compares table by table (rows, statistics, content fingerprint), replays the client's key queries on both sides, and signs an **evidence log**. One command, in the client's accounts, no data goes through us.

## 2. Who the page speaks to (three readers, in this order)
1. **The risk or compliance officer** at a bank, insurer or asset manager: they must prove that the exit plan for their cloud provider is realistic and tested (DORA).
2. **The CIO or head of data** at a mid-sized company about to renew its data platform contract: they want a measured exit cost to negotiate with.
3. **The data engineer** reading the GitHub repository: they want to see the command, the code, the report format, and know what is tested and what is not.

A single visitor must be able to go from "why" to `uvx canot scan` in under a minute.

## 3. The message
- **Candidate headline:** "Prove you can leave." (FR: *Prouvez que vous pouvez partir.*)
- **Candidate subheadline:** "A rehearsal of your exit from Snowflake, BigQuery or Databricks, in your own accounts, with a signed evidence log."
- **News hook (banner, not headline):** "On 12 Jan 2027, cloud switching charges disappear in Europe (Data Act). The exit work remains."
- **Alternative headlines to test:** "Your exit plan, executed and measured." · "What would your exit really cost?" · "What your cloud contracts don't tell you: whether you can leave."

## 4. Proposed structure
1. **Hero**: headline, subheadline, the copyable command, two buttons ("Run a free scan" to GitHub, "Talk to q4o"). Visual: see §5.
2. **The problem in three lines**: providers offer free tools to move in, nobody offers the one that checks the way out; DORA and the Data Act require being able to leave; an exit plan on paper has never been executed.
3. **How it works**: five steps (install, copy, replay, compare, sign) on a diagram where the **client's perimeter** is a drawn boundary, with "q4o sends nothing" outside it.
4. **The evidence report**: a real screenshot of the report (see `examples/tpch/report.html`), with three annotations: parity per table, a diverging query, the signed log.
5. **Proof by example**: "7 tables with identical content. And one diverging query: `DATE_TRUNC` returns a `DATE` in BigQuery, a timestamp elsewhere. This is exactly what you want to know before you leave."
6. **Who it is for, and why now**: three cards (risk / CIO / engineer) with one sentence each, and a short timeline: 17 Jan 2025 DORA, 12 Sep 2025 Data Act, **12 Jan 2027** end of switching charges.
7. **Sovereign targets**: Scaleway, OVHcloud, Outscale, IONOS, STACKIT, AWS; banner "France, Germany, Netherlands, Italy, Spain". Never write "SecNumCloud-qualified" without the mention "depending on the chosen provider".
8. **Offers**: free tool (Apache-2.0) · reversibility assessment €22k excl. VAT, 3 weeks · DORA exit test from €25k · Canot Vigie subscription €15k per year. One line of deliverables per offer (see `docs/deliverables.md`).
9. **Honesty**: a project status table (tested, written but untested). It is a trust argument, not a flaw to hide.
10. **FAQ / objections**: "If the tool is free, why pay?" · "What do you see of our data? (nothing)" · "Is this a certification? (no)" · "Can you read our contracts? (no, that is a lawyer's job, we work with a law firm)" · "What if we don't want to leave? (the exercise does not make you leave)".
11. **Final call to action**: the command, the GitHub link, a short form (name, company, current platform, renewal date).

## 5. Visual ideas
- **Lifeboat metaphor**, understated: an outline of a boat or a life buoy, never clip art. You rehearse a departure, you don't suffer it. The word "exercise" (as in an evacuation drill) carries the idea.
- **The perimeter**: the central diagram is a dotted boundary around the client's accounts, with the arrows from source to target inside it. This is the image that reassures a CISO.
- **The report as hero**: the real, readable report rather than an abstract mockup. The evidence report is the product.
- **The terminal**: the command in a dark copyable block, with real output (the `OK`, `[match]`, `[mismatch]` lines).
- **Graphic tone**: sober and technical, more institutional than "startup". Starting palette: deep navy, a safety orange (the color of a life jacket) as the only accent, bluish grays. Figures in a monospace font.
- **To avoid**: purple-blue gradients, cloud illustrations, padlocks everywhere, screenshots of made-up dashboards, statistics without a source.

## 6. Tone and vocabulary
- The site is French by default, with a language switcher; the repository and the documentation are in English. In French, formal "vous", never "tu".
- Short, concrete sentences with figures. No "revolutionary", "seamless", "100% secure".
- Words to use: exit exercise, evidence report, evidence log, parity, in your accounts, reversibility.
- Words never to use: "DORA compliant", "certified", "guaranteed", "free" about leaving (the tool is free, leaving is not), "clone".

## 7. Constraints (legal and trust)
- Say "nothing leaves your accounts", never "no data leaves" (the data is copied, at the client's).
- Name no client. No third-party logos beyond the hosting providers in the compatibility list, as text.
- Every market figure carries its source in a footnote (see `docs/deliverables.md` and the briefing). Prices are "starting from".
- The name **Canot** is provisional (trademark not cleared): make it easy to change, do not bake it into a fixed logo.
- No form collects personal data beyond what is necessary; no third-party trackers (consistent with "no telemetry").

## 8. Materials provided
- Repository: `~/repos/canot` (GitHub: q4o-paris/canot).
- `README.md`: short version of the page. `docs/deliverables.md`: what the client receives. `docs/targets.md`: hosting providers and countries.
- `examples/tpch/report.html`: a real report, signed.
- Internal briefing (context, vocabulary, sales arguments): `docs/briefing.html`.

## 9. Success metric
The number of visitors who run `canot scan` (click on the copied command) and the number of qualified meeting requests (current platform + renewal date filled in). Not page views.

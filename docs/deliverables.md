# Deliverables: what the client receives, offer by offer

Each deliverable has a format, an acceptance criterion (how the client says "this is done") and what the client must provide.
Days and prices are estimates to be validated with the first clients. The worked example is in
[`examples/tpch/`](../examples/tpch/) (the TPC-H benchmark dataset, generated locally).

## 1. Technical reversibility assessment (€22k excl. VAT, 3 weeks, about 15 working days)

The question it answers: "If we wanted to leave this data platform, what would it take, how long, how much would it cost, and can we prove it?"

| # | Deliverable | Format | Acceptance criterion |
|---|---|---|---|
| D1 | **Assessment**: tables, volumes, platform-specific column types, 90-day consumption, most expensive queries | `scan.json` + one-page summary | the data lead confirms the inventory matches their scope |
| D2 | **Exit exercise** on an agreed scope (one domain, 5 to 20 tables) | `report.html` + `report.json` | full parity, or each discrepancy explained and classified (copy error, conversion rule, engine difference) |
| D3 | **Signed evidence log** | `evidence.jsonl`, `.sig`, `.pub` | `canot verify-evidence` passes; the client versions it in their own archive |
| D4 | **Exit cost scenarios** (three: minimal, nominal, conservative) | spreadsheet + 2 pages | assumptions are named, each line has its source (measurement, client's rate, or "est.") |
| D5 | **Register of what does not move as-is**: ML models, functions and procedures, scheduled queries, authorized views, row- and column-level security policies, materialized views, capacity reservations | spreadsheet: object, effort (S/M/L), proposed replacement | the data lead confirms no important object is missing |
| D6 | **Factual contract summary**: billing model, commitment end dates, notice periods, support plan, data location | 1 page of facts, no legal assessment | validated by procurement; the legal analysis remains that of the client's lawyer |
| D7 | **Exit runbook**: steps, order, duration, roles, checkpoints | 5- to 10-page document | reviewed by the team that would execute it |
| D8 | **Renewal brief**: measured exit cost, scenario comparison, what the client can ask their provider for | 2 pages for management | presented to and taken over by the CIO or procurement |
| D9 | **Debrief** | 2-hour workshop + slides | questions logged, actions decided |

To be provided by the client: read-only access (roles listed in each source's documentation), a data lead (about half a day), an administrator (about one hour), the list of the 10 to 20 reports or queries that matter, the rate actually paid, the renewal date.
Out of scope: rewriting applications, legal advice, actually moving production data, choosing the target platform.

### Cost items for an exit from BigQuery (D4)

No amount is given here: Google's rates change and depend on the contract. Each item is filled in with the client's rate.

1. Reading or exporting the data (Storage Read API per TiB read, or export to Cloud Storage).
2. Network egress from Google Cloud. Google offers an exit program that can credit it under conditions (list of eligible products, contract termination, migration window): see cloud.google.com/exit-cloud, to be confirmed product by product.
3. Storage at the new provider, per month.
4. Dual-running period (BigQuery and the target at the same time).
5. Compute on the target.
6. Engineering time: translating queries and routines (BigQuery dialect), validation, reconnecting reporting tools.

## 2. DORA exit test (€25k to €60k, then €10k to €30k per year for re-tests)

For a financial entity. The question: "Is our exit plan for this provider realistic, and have we tested it?"

| # | Deliverable | Acceptance criterion |
|---|---|---|
| T1 | Test scenarios (planned exit, provider failure, loss of service) mapped to Art. 10 of Delegated Regulation 2024/1773 | validated by the risk officer |
| T2 | Execution on a critical scope, with measurements (duration, parity, cost) | signed evidence log (D3) |
| T3 | Test sheet: date, scope, result, discrepancies, actions | accepted by internal control |
| T4 | Register items: existence of a plan, alternative providers identified | reviewed by compliance |
| T5 | One-page summary for the committee | presented |

This is not a DORA compliance attestation. A recurring re-test may make q4o an ICT third-party provider of the entity (Art. 30 clauses): contract as one-off engagements first.

## 3. Canot Vigie (subscription, €15k per year per source account)

Rerun the exercise every quarter. Deliverables: a signed evidence log per quarter, comparison with the previous one (drift), an alert when a new non-portable object appears, up-to-date regulatory templates, support with a written response time. Acceptance criterion: the quarter runs without any q4o involvement beyond reading the report.

## 4. SAS parity pilot (€45k fixed, 6 weeks)

| # | Deliverable | Acceptance criterion |
|---|---|---|
| S1 | Map of the dependencies and dead code of a pipeline | validated by the owning team |
| S2 | 40 programs converted and proven identical on the client's data | parity report per program, tolerances signed beforehand |
| S3 | Firm quote for the rest, by domain | accepted or declined in writing |
| S4 | Supporting note for the license renewal | delivered |

Condition: work on the client's SAS license, never on a Learning or Academic edition.

## 5. Export kit for SaaS vendors (€10k to €40k, 3 to 6 weeks)

A documented export function (OpenAPI), the register content required by Art. 26 of the Data Act, an export test suite, the runbook. Acceptance: a test client exports their data on their own by following the documentation.

## 6. What we never deliver

A legal opinion, a certification, a guarantee that leaving is free, permanent access to the client's environment.

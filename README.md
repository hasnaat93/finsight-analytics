# FinSight Analytics

**Financial performance and operations intelligence for a payment processor: raw files → Python ETL → PostgreSQL star schema → Power BI semantic model and DAX.**

Power BI · DAX · Tabular model (TMDL) · SQL · PostgreSQL · Python ETL · Data quality

**[Live web preview of the report](https://hasnaat93.github.io/finsight-analytics/)** — rebuilt by CI from a fresh pipeline run on every push.

> **Synthetic data.** Every merchant, payment and amount is produced by a seeded simulator. Nothing here describes a real business.

![Executive overview](docs/screenshots/01-executive-overview.png)

*Screenshot of the web preview. See [What is and is not built](#what-is-and-is-not-built) for the state of the Power BI report itself.*

---

## What this project covers

Most BI portfolio projects are a dashboard on a clean CSV. This one is the whole path, and every layer can be inspected:

| Layer | What is in the repo |
|---|---|
| **Source data** | A generator for 36 months of payment processor exports, with seasonality, merchants that grow, decline and churn, four currencies, and deliberately injected defects. |
| **ETL** | A Python pipeline that verifies each delivery, lands it, validates it against configurable rules, quarantines bad rows with a reason, loads the warehouse and reconciles source against target. Incremental and idempotent. |
| **Warehouse** | A PostgreSQL star schema: 8 dimensions (the merchant dimension keeps history, type 2), 5 facts, enforced keys, an Unknown member in every dimension. |
| **SQL** | CTEs, window functions, `GROUPING SETS`, cohort retention, a Pareto analysis, type 2 load logic, 15 assertion queries, and measured index and materialized view effects. |
| **Semantic model** | A Power BI project (`.pbip`) with the model in TMDL: 19 tables, 24 relationships, 50 documented DAX measures in display folders, a time-intelligence calculation group, hierarchies and a row-level security role. |
| **Reporting** | Four report pages, specified in detail and implemented as a web preview: Executive, Merchant, Transaction, Settlement & Reconciliation. |
| **Proof** | Source-to-target reconciliation on every load, and a test that compares what the pipeline quarantined with what the generator broke. |

### Questions it answers

- How much value and net revenue is being processed, and how does it compare with last year?
- Which merchants drive the volume, how concentrated is it, and which are in decline?
- Where do declines, refunds and disputes concentrate, by method, channel, brand and time of day?
- Were merchants paid the right amount, and where were they not?
- What is the take rate after paying issuers and card networks and absorbing lost disputes?

## Architecture

```mermaid
flowchart TD
    G["Synthetic source system<br/>data_generator/"] -->|"one folder per month:<br/>8 CSV files + manifest (rows, SHA-256)"| X

    subgraph ETL["Python ETL (etl/)"]
        X["Extract<br/>verify checksums and layout, COPY to staging"]
        V["Validate<br/>rules from config/validation_rules.yaml"]
        C["Cleanse and conform<br/>types, casing, currency conversion"]
        L["Load<br/>dimensions (SCD type 2), then facts"]
        R["Reconcile<br/>rows and amounts, source vs warehouse"]
        X --> V --> C --> L --> R
    end

    V -->|rejected rows + reason| Q[("audit.rejected_rows")]
    R -->|control results| A[("audit.reconciliation")]

    subgraph DWH["PostgreSQL"]
        S[("staging<br/>raw text")] --> D[("dwh<br/>star schema")] --> M[("mart<br/>reporting views")]
    end

    X --> S
    L --> D
    M --> P["Power BI semantic model<br/>TMDL, DAX, calculation group, RLS"]
    M --> W["Web preview<br/>GitHub Pages"]
    P --> B["Power BI report<br/>4 pages"]
```

Each monthly batch loads in **one transaction**. If a step fails or a reconciliation control does not balance, nothing of that batch reaches the warehouse; the failure and its evidence are still recorded.

## The star schema

```mermaid
erDiagram
    dim_date ||--o{ fact_transaction : ""
    dim_merchant ||--o{ fact_transaction : "version on payment date"
    dim_customer ||--o{ fact_transaction : ""
    dim_payment_method ||--o{ fact_transaction : ""
    dim_channel ||--o{ fact_transaction : ""
    dim_geography ||--o{ fact_transaction : ""
    dim_currency ||--o{ fact_transaction : ""
    dim_transaction_status ||--o{ fact_transaction : ""
    fact_transaction ||--o{ fact_refund : ""
    fact_transaction ||--o{ fact_chargeback : ""
    fact_transaction ||--o| fact_fee : ""
    dim_merchant ||--o{ fact_settlement : ""
    dim_date ||--o{ fact_settlement : ""

    dim_merchant {
        int merchant_key PK
        varchar merchant_id "business key"
        varchar pricing_plan "type 2"
        varchar risk_tier "type 2"
        varchar merchant_name "type 1"
        date valid_from
        date valid_to
        boolean is_current
    }
    fact_transaction {
        bigint transaction_key PK
        varchar transaction_id UK
        int date_key FK
        int merchant_key FK
        numeric amount_original
        numeric amount_reporting "USD"
        boolean is_approved
        boolean is_captured
    }
    fact_settlement {
        varchar settlement_batch_id UK
        numeric net_payout "what the source paid"
        numeric expected_payout "what the warehouse calculates"
        numeric variance
        boolean is_reconciled
    }
```

Full column-level detail: [docs/data-dictionary.md](docs/data-dictionary.md).

## Data quality

Bad data is expected, so the pipeline decides explicitly what happens to each kind:

| Problem | What happens |
|---|---|
| Duplicate id | The latest version is kept; the others are quarantined |
| Amount zero, negative or not a number | Quarantined |
| Unknown currency, invalid status, malformed or future timestamp | Quarantined |
| Refund, chargeback or fee for a payment that is not a captured payment | Quarantined |
| Refund larger than the payment | Quarantined |
| Payment with no merchant or an unknown customer | **Loaded** against the Unknown member (key −1): it is still a real payment |
| Settlement whose payout does not add up | **Loaded** and reported as unreconciled: hiding it would defeat the reconciliation page |

Rules are configuration, not code: [config/validation_rules.yaml](config/validation_rules.yaml).

Every batch is then reconciled, per file:

1. rows in the manifest = rows landed in staging
2. rows landed = rows loaded + rows quarantined
3. total amount of the validated source rows = total amount in the warehouse

And the settlement facts carry their own reconciliation: the warehouse recomputes what each merchant should have been paid from its own payments, fees, refunds and chargebacks, and compares that with what the source says was paid, to the cent.

### The test that matters most

The generator returns the exact number of each defect it injected. A test loads the data and asserts that the quarantine contains exactly those rows under exactly those rules, that every tampered settlement is reported as unreconciled, and that nothing else is. So the validation rules are proven both ways: they catch what they should, and they reject nothing they should not.

## DAX highlights

All 50 measures, with descriptions: [docs/dax-measures.md](docs/dax-measures.md).

```dax
Active Merchants =
COUNTROWS (
    FILTER (
        VALUES ( dim_merchant[merchant_id] ),
        dim_merchant[merchant_id] <> "UNKNOWN" && [Captured Transactions] > 0
    )
)
```

Counts merchants, not rows: the dimension holds one row per *version* of a merchant, so counting keys would count a merchant twice after a pricing change.

```dax
Top 10 Merchant Share % =
VAR Top10 =
    TOPN ( 10, ALLSELECTED ( dim_merchant[merchant_name] ), [Total Transaction Value] )
RETURN
    DIVIDE (
        CALCULATE ( [Total Transaction Value], Top10 ),
        CALCULATE ( [Total Transaction Value], ALLSELECTED ( dim_merchant[merchant_name] ) )
    )
```

A **calculation group** (`Time Intelligence`: Current, MTD, QTD, YTD, PY, YoY, YoY %) applies time logic to any measure, instead of writing seven variants of each. **Row-level security** restricts a Regional Manager to the regions listed against their sign-in address.

## Run it

Needs Python 3.11+ and PostgreSQL 14+ (Docker Compose file included).

```bash
cp .env.example .env
make install                 # Python dependencies
make db                      # PostgreSQL in Docker (or point FINSIGHT_DATABASE_URL at your own)
export FINSIGHT_DATABASE_URL=postgresql://finsight:finsight@localhost:5432/finsight

make generate SCALE=0.2      # 36 months of source files (SCALE=1.0 is about 1.5 million payments)
make etl                     # load every batch that is not loaded yet
make test-sql                # 15 SQL assertions against the warehouse
make status                  # what was loaded, what was quarantined
make dashboard               # site/index.html
```

No `make`? Each target is one command; see the [Makefile](Makefile). For example `python -m etl.run_pipeline run`.

Run `make etl` again: nothing happens, because every batch is already loaded. Reload one deliberately with `python -m etl.run_pipeline run --batch 2025-06 --reload`: nothing changes, because every load is keyed on a business key.

### Open the Power BI model

Open `powerbi/FinSight.pbip` in Power BI Desktop, set the `Server` and `Database` parameters, and refresh. Details and the page-by-page build guide: [powerbi/README.md](powerbi/README.md) and [docs/report-design.md](docs/report-design.md).

## Tests

```bash
make test                    # ruff + pytest
```

39 tests. 20 need no database. 19 run the pipeline against a real PostgreSQL (set `FINSIGHT_TEST_DATABASE_URL` to a database the tests may wipe): every validation rule, type 2 history, currency conversion, settlement reconciliation, incremental and idempotent loading, a tampered file, a changed layout, and a deliberately injected load defect that the reconciliation must catch and roll back.

CI runs all of it on every push, then generates data, loads the warehouse, runs the SQL assertions, reloads a batch to prove idempotency, validates the Power BI model, and publishes the web preview.

## What is and is not built

Being exact about this matters more than a longer feature list. Full detail: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

- **Built and verified in CI:** the generator, the ETL, the warehouse, the SQL, the tests, and the web preview.
- **Built, structurally verified:** the Power BI semantic model. CI loads the TMDL with Microsoft's Tabular Object Model library, so it is well formed and every reference resolves. The DAX has **not** been executed, because the model has not been opened in Power BI Desktop.
- **Specified, not built:** the Power BI report pages. The project opens with one empty page; the four pages are described visual by visual in [docs/report-design.md](docs/report-design.md). The screenshots in this README are of the web preview, not of Power BI.

## Documentation

| Document | Content |
|---|---|
| [docs/data-dictionary.md](docs/data-dictionary.md) | Every table and column, with grain and keys |
| [docs/dax-measures.md](docs/dax-measures.md) | Every measure with its description and DAX |
| [docs/report-design.md](docs/report-design.md) | The four report pages, visual by visual |
| [docs/performance.md](docs/performance.md) | Measured effect of the indexes and the materialized view |
| [docs/validation.md](docs/validation.md) | How the report numbers are checked against SQL |
| [powerbi/README.md](powerbi/README.md) | Opening, refreshing and publishing the Power BI project |

## Limitations

- Synthetic data, monthly batches. Real feeds are daily or streaming and messier in ways a simulator does not imagine.
- Transactions are final when delivered. A payment that changes state after delivery is not modelled here.
- Merchant history is built forwards only: reloading an older batch does not rewrite it.
- One reporting currency (USD). Fees convert at the payment's rate; refunds and chargebacks at the rate of their own date.
- `fact_fee` is one row per captured payment with three amounts, not one row per fee type.
- The largest run so far is about 590,000 payments, in CI. The 1.5-million-payment scale has not been run end to end.

## Licence

[MIT](LICENSE)

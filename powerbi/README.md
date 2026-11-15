# The Power BI project

`FinSight.pbip` is a Power BI Project: the semantic model is stored as TMDL text files, so every table, relationship and measure can be read and reviewed on GitHub.

```
powerbi/
├── FinSight.pbip                      open this in Power BI Desktop
├── FinSight.SemanticModel/
│   └── definition/
│       ├── model.tmdl, database.tmdl
│       ├── expressions.tmdl           the Server and Database parameters
│       ├── relationships.tmdl         24 one-to-many, single-direction relationships
│       ├── roles/Regional Manager.tmdl
│       └── tables/                    one file per table; _Measures.tmdl holds the 50 measures
├── FinSight.Report/                   one empty page (see below)
├── theme/finsight-theme.json
└── tools/validate-model/              the check CI runs on the model
```

## State of this project: read this first

- **The semantic model is complete** and structurally verified: CI loads it with Microsoft's Tabular Object Model library and checks that every reference resolves and every measure is documented.
- **It has not been opened in Power BI Desktop.** It was written on a machine without Power BI. Until it is opened and refreshed, the DAX has not been compiled or run, so a measure may need a correction.
- **The report has one empty page.** The four pages are specified in [`docs/report-design.md`](../docs/report-design.md) and have to be built in Power BI Desktop.

## Open and refresh

1. Load a warehouse first: from the repository root, `make db generate etl` (see the main README).
2. Install the PostgreSQL connector prerequisites if Power BI asks (current versions of Power BI Desktop include the Npgsql provider).
3. Open `FinSight.pbip` in Power BI Desktop. TMDL project support needs a 2024 or later version; if prompted, enable *Power BI Project (.pbip) save option* under Options → Preview features.
4. Home → Transform data → Edit parameters. Set **Server** (for example `localhost:5432`) and **Database** (`finsight`).
5. When asked for credentials choose *Database* and enter the PostgreSQL user (`finsight` / `finsight` with the Docker Compose defaults). Accept the unencrypted connection for a local database.
6. Refresh. Every table reads a view in the `mart` schema.

## After the first successful open

- Check the measures: put the ones listed in [`docs/validation.md`](../docs/validation.md) on cards and compare with the SQL totals.
- Apply the theme: View → Themes → Browse for themes → `theme/finsight-theme.json`.
- Build the pages from [`docs/report-design.md`](../docs/report-design.md).
- Save. Power BI rewrites the project files in its own formatting; commit the result.
- Take screenshots into `docs/screenshots/` and replace the web preview images in the README.

## Design of the model

- **Import mode**, from `mart` views only. The warehouse can be reorganised without breaking the model.
- **Star schema.** Every relationship is one-to-many from a dimension to a fact and filters in one direction.
- **`dim_date` is the date table**, with a Calendar hierarchy (Year → Quarter → Month → Day). Month and day names sort by their numbers.
- **Keys and raw amounts are hidden.** Report authors see dimension attributes and measures, not plumbing. Implicit measures are disabled, so every number on a report is a named, documented measure.
- **`_Measures`** holds all 50 measures in eight display folders: Volume, Revenue, Risk, Time Intelligence, Merchant, Settlement, Data Quality, Report Helpers.
- **`Time Intelligence`** is a calculation group with seven items.
- **`dim_merchant` has one row per version.** Facts join to the version in force on their date. Count merchants with `[Active Merchants]`, not by counting rows.
- **`Regional Manager`** is a row-level security role driven by the `region_access` table.
- **Power Query does almost nothing**: it selects a view. Transformation happens in the ETL and in SQL, so every query folds to the database.

## Publishing

Publishing to the Power BI Service needs a Power BI account. A public link ("Publish to web") additionally needs that feature enabled by the tenant administrator and must only ever be used with non-confidential data, which this is. A scheduled refresh against a local PostgreSQL needs an on-premises data gateway.

## Validating the model without Power BI

```bash
dotnet run --project powerbi/tools/validate-model -- powerbi/FinSight.SemanticModel/definition
```

Needs the .NET 8 SDK. CI runs this on every push.

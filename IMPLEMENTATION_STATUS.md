# Implementation status

An honest record of what was built, what was run, and what was not. Last updated 2026-11-15.

## Verified in GitHub Actions (Ubuntu, Python 3.12, PostgreSQL 16)

Every push runs this, and it passed on the commit this file was written for.

| Check | Result |
|---|---|
| `ruff check .` | Clean |
| `pytest` | 39 tests pass: 20 unit tests and 19 integration tests that run the pipeline against PostgreSQL |
| Generate 36 months of source data (scale 0.5) | 588,606 transaction rows written, with defects injected |
| Load the warehouse | 36 of 36 monthly batches loaded; every reconciliation control passed |
| SQL assertions | 15 of 15 pass |
| Reload of one batch (`--reload`) | Succeeds; the 15 assertions still pass |
| Quarantine against injected defects | Every rule's quarantined count equals the generator's injected count (1,944 rows in total) |
| Settlement reconciliation | 558 of 138,837 payouts reported as unreconciled: exactly the 558 the generator altered |
| Benchmark | Ran; figures in [docs/performance.md](docs/performance.md) |
| Power BI semantic model | Loads with Microsoft's Tabular Object Model library: 19 tables, 50 measures, 24 relationships, 1 role, 1 calculation group; all project conventions hold |
| Web preview | Built and published to GitHub Pages |

## Verified by hand

- The published web preview was opened in Microsoft Edge at 1360 px and 390 px wide. All four pages render, the year filter and tabs work, there are no console errors and no horizontal overflow. The README screenshots were captured from it.

## Verified on the development machine (Windows 11, Python 3.14, PostgreSQL 14)

- Lint and the 20 unit tests.
- An early small-scale run of all 36 batches. The integration tests and the larger runs were done in CI, not locally.

## Not verified

- **Power BI Desktop.** The project has never been opened in Power BI. The DAX of the 50 measures, the calculation group and the security role has not been compiled or executed. The model's structure is verified; its behaviour is not.
- **The Power BI report.** It contains one empty page. The four pages are specified in [docs/report-design.md](docs/report-design.md) but not built, so there are no Power BI screenshots. The screenshots in the README are of the web preview.
- **Power BI totals against SQL.** [docs/validation.md](docs/validation.md) has the SQL side and an empty column for the Power BI side.
- **Full scale.** The largest run is about 590,000 payments. The 1.5-million-payment default (`SCALE=1.0`) has not been run end to end.
- **Docker Compose locally.** The compose file was not run on the development machine (no Docker). CI uses the same PostgreSQL image as a service container.
- **Windows with PostgreSQL 16, macOS.**

## Deviations from the original brief

| Brief | What was done | Why |
|---|---|---|
| pandas and SQLAlchemy in the ETL | psycopg and SQL; no pandas | The heavy work is set-based SQL inside the database; pulling rows into pandas would add a copy and a dependency for no gain |
| `MERGE` for upserts | `INSERT ... ON CONFLICT` | Works on PostgreSQL 14 and later; `MERGE` needs 15 |
| `fact_fee`: one row per fee line | One row per captured payment with three amounts | Same information in a third of the rows |
| Missing merchant id → Unknown member | Done; an unrecognised merchant id and a missing or unknown customer are treated the same way | They are still real payments |
| Monthly partitioning of `fact_transaction` | Not partitioned; reasoning in docs/performance.md | Not needed at this size, and it complicates the keys |
| `.pbip` with four built report pages and screenshots | Semantic model complete; report pages specified, not built | No Power BI Desktop on the development machine |
| `docs/erd.png`, `docs/architecture.png` | Mermaid diagrams in the README | They render on GitHub and stay in step with the text |
| Demo video or GIF | Not made | Needs the built report |
| 500 merchants, 1.5M transactions | Configurable by `SCALE`; CI runs 0.5 | Keeps CI under ten minutes |

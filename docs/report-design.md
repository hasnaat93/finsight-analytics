# Report design

The four report pages, visual by visual. The semantic model already contains every table, measure and hierarchy named here; building a page is a matter of placing visuals and binding fields.

The [web preview](https://hasnaat93.github.io/finsight-analytics/) implements the same four pages and is a useful reference for layout and expected numbers.

All measures are in the `_Measures` table. Canvas: 1280 × 720, 16 px outer margin, 12 px gutter. Theme: `powerbi/theme/finsight-theme.json` (View → Themes → Browse for themes).

## Shared elements

- **Slicers** in a strip at the top of every page, synced across pages (View → Sync slicers): `dim_date[year]` (dropdown), `dim_geography[region]` (dropdown), `dim_merchant[category]` (dropdown).
- **Reset filters** button, top right, bound to a bookmark captured with nothing selected.
- **Page title** as a text box; chart titles from the visual's own title.

## Page 1: Executive Overview

Audience: leadership. The state of the business at a glance.

| # | Visual | Fields | Notes |
|---|---|---|---|
| 1 | Card (new) ×6, one row | `[Total Transaction Value]`, `[Net Revenue]`, `[Take Rate %]`, `[Approval Rate %]`, `[Refund Rate %]`, `[Active Merchants]` | Reference label on each: `[TPV YoY %]` on the first, `[Net Revenue YoY %]` on the second. Colour the reference label with `[KPI Colour]` (fx → Field value). |
| 2 | Line chart, left two thirds | X: `dim_date[month_name]`; Y: `[Total Transaction Value]`, `[TPV PY]` | Title from `[Dynamic Title]` (fx on the title text). Prior year as a dashed line. |
| 3 | Waterfall chart, bottom left | Category: a small disconnected table, or build as four cards if preferred; values `[Fee Revenue]`, `−[Processing Cost]`, `−[Chargeback Losses]`, `[Net Revenue]` | Shows how fee revenue becomes net revenue. |
| 4 | Filled map or bar chart, bottom right | Location: `dim_geography[country]`; value: `[Total Transaction Value]` | A bar chart is the accessible fallback if maps are disabled in the tenant. |
| 5 | Slicer | `'Metric Selector'[Metric]` | Drives `[Selected KPI]`, which can replace the Y value of visual 2 to switch the chart between value, net revenue and transaction count. |

**Mobile layout** (View → Mobile layout): the six cards in a 2 × 3 grid, then the line chart, then the country bar chart. Leave the waterfall off the phone view.

## Page 2: Merchant Analytics

Audience: account management.

| # | Visual | Fields | Notes |
|---|---|---|---|
| 1 | Card ×3 | `[Active Merchants]`, `[Top 10 Merchant Share %]`, `[At-Risk Merchants]` | |
| 2 | Table | `dim_merchant[merchant_name]`, `[Merchant TPV Rank]`, `[Total Transaction Value]`, `[TPV YoY %]`, `[Take Rate %]`, `[Merchant Share of TPV %]` | Top N filter: top 10 by `[Total Transaction Value]`. Data bars on value; font colour on `[TPV YoY %]` by rules (red below 0). |
| 3 | Line and clustered column chart (Pareto) | X: `dim_merchant[merchant_name]` sorted by value descending; column: `[Total Transaction Value]`; line: a running-share quick measure | Shows concentration. |
| 4 | Treemap | Category: `dim_merchant[category]`; values: `[Total Transaction Value]` | |
| 5 | Table: merchants at risk | `dim_merchant[merchant_name]`, `dim_merchant[category]`, `[TPV PY]`, `[Total Transaction Value]`, `[TPV YoY %]` | Visual-level filter: `[TPV YoY %]` is less than −0.25. Needs a single year selected. |

Use `dim_merchant[merchant_id]` or `merchant_name` on visuals, never `merchant_key`: the dimension has one row per version of a merchant.

### Drill-through: Merchant Detail

A hidden page with `dim_merchant[merchant_name]` in the drill-through well. Contents: cards for value, take rate and approval rate; `[Total Transaction Value]` by month; a matrix of `dim_payment_method[payment_method]` by `[Approval Rate %]`; `[Total Refunds]` by `fact_refund[refund_reason]`; a table of `fact_settlement[settlement_batch_id]` with `[Settlement Variance]` filtered to unreconciled batches; and a table of the merchant's versions (`pricing_plan`, `risk_tier`, `valid_from`) showing its history.

## Page 3: Transaction Analytics

Audience: operations and risk.

| # | Visual | Fields | Notes |
|---|---|---|---|
| 1 | Card ×4 | `[Transaction Count]`, `[Approval Rate %]`, `[Average Ticket Size]`, `[Chargeback Rate %]` | |
| 2 | Clustered bar chart ×3 | Axis: `dim_payment_method[payment_method]`, `dim_channel[channel]`, `dim_payment_method[card_brand]`; value: `[Approval Rate %]` | Start the axis near the lowest value so differences are visible; label the bars. |
| 3 | Bar chart | Axis: `dim_transaction_status[decline_reason]`; value: `[Declined Transactions]` | Visual-level filter: `is_success` is False. |
| 4 | Matrix (heatmap) | Rows: `dim_date[day_name]`; columns: `fact_transaction[hour_of_day]`; values: `[Transaction Count]` | Background colour scale on the values; turn off totals. |
| 5 | Line chart | X: `dim_date[year_month]`; Y: `[Refund Rate %]`, `[Chargeback Rate %]` | Put the second measure on a separate small chart rather than a secondary axis. |
| 6 | Decomposition tree | Analyse: `[Declined Transactions]`; explain by: payment method, channel, card brand, decline reason, country | |

## Page 4: Settlement & Reconciliation

Audience: finance.

| # | Visual | Fields | Notes |
|---|---|---|---|
| 1 | Card ×4 | `[Reconciliation Rate %]`, `[Unreconciled Batches]`, `[Settlement Variance]`, `[Absolute Settlement Variance]` | |
| 2 | Line chart | X: `dim_date[year_month]`; Y: `[Expected Payout]`, `[Actual Payout]` | |
| 3 | Table: unreconciled batches | `fact_settlement[settlement_batch_id]`, `dim_date[date]`, `dim_merchant[merchant_name]`, `[Expected Payout]`, `[Actual Payout]`, `[Settlement Variance]` | Visual-level filter: `is_reconciled` is False. Sort by absolute variance. Drill-through to Merchant Detail. |
| 4 | Data quality panel | Cards: `[Rows Delivered]`, `[Rows Rejected]`, `[Rejection Rate %]`, `[Controls Passed %]`, `[Last Refresh]`. Bar chart: `etl_rejections[rule_name]` by `[Rows Rejected]`. | Sourced from the `audit` schema through `mart.etl_rejections` and `mart.etl_batches`. These tables are not related to `dim_date`; the year slicer does not filter them. |

## Report tooltips

Create two tooltip pages (Page information → Allow use as tooltip, size Tooltip):

- **Merchant tooltip:** value, year-on-year change, take rate and refund rate for the hovered merchant. Attach to the merchant tables.
- **Month tooltip:** value, net revenue, approval rate and active merchants for the hovered month. Attach to the monthly line charts.

## Bookmarks

- **Reset filters:** all slicers cleared.
- **Value / Net revenue / Transactions:** three bookmarks that set the Metric Selector, shown as buttons above the main chart.
- **At-risk view:** Merchant Analytics with the at-risk table expanded.

## Time intelligence with the calculation group

Put `'Time Intelligence'[Time Calculation]` on the columns of a matrix, `dim_merchant[category]` on the rows and `[Total Transaction Value]` in the values. One measure produces Current, MTD, QTD, YTD, PY, YoY and YoY % columns. Swap the measure for `[Net Revenue]` and the same columns apply.

## Row-level security

Modeling → Manage roles shows **Regional Manager**. Test with View as → Other user, using one of the addresses in `dwh.region_access` (for example `manager.europe@example.com`); only that manager's regions should remain. Replace the placeholder addresses with real ones before publishing.

## Accessibility checklist

- [ ] Alt text on every visual (Format → General → Alt text), stating what the visual shows.
- [ ] Tab order set on each page (View → Selection → Tab order), following reading order.
- [ ] No meaning carried by colour alone: the at-risk table and variance columns also show a sign or label.
- [ ] Text contrast at least 4.5:1; the theme colours were chosen for this on a white background.
- [ ] Titles on every chart; axis titles where the unit is not obvious.
- [ ] Each page is usable with the keyboard only.

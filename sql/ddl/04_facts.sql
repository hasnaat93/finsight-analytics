-- Facts. Each references dimensions only by surrogate key, has a declared grain and a
-- unique business key, and carries batch_id so a batch can be reconciled or reloaded.
-- Foreign keys are enforced: PostgreSQL does it cheaply and it is the last line of defence.
--
-- Partitioning note: at 1.5 million rows fact_transaction does not need partitioning.
-- docs/performance.md discusses when monthly range partitioning on date_key would pay off.

-- Grain: one row per payment attempt.
create table if not exists dwh.fact_transaction (
    transaction_key   bigint generated always as identity primary key,
    transaction_id    varchar(24)   not null unique,     -- degenerate dimension
    date_key          integer       not null references dwh.dim_date (date_key),
    merchant_key      integer       not null references dwh.dim_merchant (merchant_key),
    customer_key      integer       not null references dwh.dim_customer (customer_key),
    method_key        integer       not null references dwh.dim_payment_method (method_key),
    channel_key       integer       not null references dwh.dim_channel (channel_key),
    geography_key     integer       not null references dwh.dim_geography (geography_key),
    currency_key      integer       not null references dwh.dim_currency (currency_key),
    status_key        integer       not null references dwh.dim_transaction_status (status_key),
    transaction_ts    timestamp     not null,
    hour_of_day       smallint      not null,
    amount_original   numeric(18,2) not null check (amount_original > 0),
    amount_reporting  numeric(18,2) not null,            -- in USD at the day's rate
    is_approved       boolean       not null,
    is_captured       boolean       not null,
    batch_id          varchar(7)    not null
);

-- Grain: one row per refund.
create table if not exists dwh.fact_refund (
    refund_key        bigint generated always as identity primary key,
    refund_id         varchar(24)   not null unique,
    transaction_key   bigint        not null references dwh.fact_transaction (transaction_key),
    date_key          integer       not null references dwh.dim_date (date_key),
    merchant_key      integer       not null references dwh.dim_merchant (merchant_key),
    geography_key     integer       not null references dwh.dim_geography (geography_key),
    currency_key      integer       not null references dwh.dim_currency (currency_key),
    refund_reason     varchar(40)   not null,
    refund_original   numeric(18,2) not null check (refund_original > 0),
    refund_amount     numeric(18,2) not null,            -- USD
    is_partial        boolean       not null,
    batch_id          varchar(7)    not null
);

-- Grain: one row per dispute.
create table if not exists dwh.fact_chargeback (
    chargeback_key      bigint generated always as identity primary key,
    chargeback_id       varchar(24)   not null unique,
    transaction_key     bigint        not null references dwh.fact_transaction (transaction_key),
    date_key            integer       not null references dwh.dim_date (date_key),
    merchant_key        integer       not null references dwh.dim_merchant (merchant_key),
    geography_key       integer       not null references dwh.dim_geography (geography_key),
    currency_key        integer       not null references dwh.dim_currency (currency_key),
    reason_code         varchar(40)   not null,
    outcome             varchar(10)   not null check (outcome in ('WON', 'LOST', 'PENDING')),
    chargeback_original numeric(18,2) not null check (chargeback_original > 0),
    chargeback_amount   numeric(18,2) not null,          -- USD
    batch_id            varchar(7)    not null
);

-- Grain: one row per captured payment: the fee charged to the merchant and what it cost
-- to process. Stored wide (three amounts) rather than one row per fee type, which would
-- triple the row count without adding information.
create table if not exists dwh.fact_fee (
    fee_key           bigint generated always as identity primary key,
    transaction_key   bigint        not null unique references dwh.fact_transaction (transaction_key),
    date_key          integer       not null references dwh.dim_date (date_key),
    merchant_key      integer       not null references dwh.dim_merchant (merchant_key),
    geography_key     integer       not null references dwh.dim_geography (geography_key),
    currency_key      integer       not null references dwh.dim_currency (currency_key),
    fee_original      numeric(18,2) not null check (fee_original >= 0),
    fee_amount        numeric(18,2) not null,            -- merchant fee, USD: the processor's revenue
    interchange_cost  numeric(18,2) not null,            -- paid to the card issuer, USD
    scheme_cost       numeric(18,2) not null,            -- paid to the card network, USD
    batch_id          varchar(7)    not null
);

-- Grain: one row per merchant per settlement batch (one payout).
-- The gross, fee, refund and chargeback amounts are what the source says it paid on.
-- expected_payout is what the warehouse calculates from its own facts for the same day.
create table if not exists dwh.fact_settlement (
    settlement_key      bigint generated always as identity primary key,
    settlement_batch_id varchar(30)   not null unique,
    date_key            integer       not null references dwh.dim_date (date_key),
    merchant_key        integer       not null references dwh.dim_merchant (merchant_key),
    geography_key       integer       not null references dwh.dim_geography (geography_key),
    currency_key        integer       not null references dwh.dim_currency (currency_key),
    gross_original      numeric(18,2) not null,
    fees_original       numeric(18,2) not null,
    refunds_original    numeric(18,2) not null,
    chargebacks_original numeric(18,2) not null,
    net_payout_original numeric(18,2) not null,
    expected_payout_original numeric(18,2) not null,
    gross_amount        numeric(18,2) not null,          -- USD from here down
    fees                numeric(18,2) not null,
    refunds             numeric(18,2) not null,
    chargebacks         numeric(18,2) not null,
    net_payout          numeric(18,2) not null,
    expected_payout     numeric(18,2) not null,
    variance            numeric(18,2) generated always as (net_payout - expected_payout) stored,
    is_reconciled       boolean       not null,          -- judged in the settlement currency, to the cent
    batch_id            varchar(7)    not null
);

-- Indexes on every foreign key a report filters or joins on. The measured effect of the
-- ones that matter most is in docs/performance.md.
create index if not exists fact_transaction_date     on dwh.fact_transaction (date_key);
create index if not exists fact_transaction_merchant on dwh.fact_transaction (merchant_key, date_key);
create index if not exists fact_transaction_customer on dwh.fact_transaction (customer_key);
create index if not exists fact_transaction_batch    on dwh.fact_transaction (batch_id);
create index if not exists fact_refund_date          on dwh.fact_refund (date_key);
create index if not exists fact_refund_merchant      on dwh.fact_refund (merchant_key, date_key);
create index if not exists fact_refund_transaction   on dwh.fact_refund (transaction_key);
create index if not exists fact_chargeback_date      on dwh.fact_chargeback (date_key);
create index if not exists fact_chargeback_merchant  on dwh.fact_chargeback (merchant_key, date_key);
create index if not exists fact_chargeback_transaction on dwh.fact_chargeback (transaction_key);
create index if not exists fact_fee_date             on dwh.fact_fee (date_key);
create index if not exists fact_fee_merchant         on dwh.fact_fee (merchant_key, date_key);
create index if not exists fact_settlement_date      on dwh.fact_settlement (date_key);
create index if not exists fact_settlement_merchant  on dwh.fact_settlement (merchant_key, date_key);
create index if not exists fact_settlement_unreconciled on dwh.fact_settlement (date_key) where not is_reconciled;

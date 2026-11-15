-- Staging: the delivered files exactly as they arrived. Every source column is text, so a
-- malformed value can be landed, inspected and rejected by a rule instead of failing the load.
-- row_number is the position in the file; it identifies a row in the quarantine.

create table if not exists staging.merchants (
    row_number bigint generated always as identity,
    merchant_id text, merchant_name text, mcc text, category text, country text,
    onboarding_date text, risk_tier text, pricing_plan text, status text
);

create table if not exists staging.customers (
    row_number bigint generated always as identity,
    customer_id text, country text, segment text
);

create table if not exists staging.transactions (
    row_number bigint generated always as identity,
    transaction_id text, merchant_id text, customer_id text, transaction_ts text, amount text,
    currency text, payment_method text, card_brand text, card_type text, channel text,
    status text, decline_reason text
);

create table if not exists staging.refunds (
    row_number bigint generated always as identity,
    refund_id text, transaction_id text, refund_ts text, amount text, currency text, reason text
);

create table if not exists staging.chargebacks (
    row_number bigint generated always as identity,
    chargeback_id text, transaction_id text, chargeback_date text, amount text, currency text,
    reason_code text, outcome text
);

create table if not exists staging.fees (
    row_number bigint generated always as identity,
    transaction_id text, merchant_fee text, interchange_cost text, scheme_cost text, currency text
);

create table if not exists staging.settlements (
    row_number bigint generated always as identity,
    settlement_batch_id text, merchant_id text, settlement_date text, currency text,
    gross_amount text, fee_amount text, refund_amount text, chargeback_amount text, net_payout text
);

create table if not exists staging.fx_rates (
    row_number bigint generated always as identity,
    rate_date text, currency text, rate_to_usd text
);

-- Safe casts used by the validation rules: they return NULL for text that is not a valid
-- value, so a rule can test a bad value instead of the statement failing on it.

-- Money: an optionally negative decimal with at most two decimal places.
create or replace function staging.to_amount(v text) returns numeric
language sql immutable as $$
    select case when trim(v) ~ '^-?[0-9]{1,15}(\.[0-9]{1,2})?$' then trim(v)::numeric(18, 2) end
$$;

create or replace function staging.to_number(v text) returns numeric
language sql immutable as $$
    select case when trim(v) ~ '^-?[0-9]{1,15}(\.[0-9]{1,10})?$' then trim(v)::numeric end
$$;

-- The pattern rejects most junk cheaply; the exception handler catches the rest, such as month 13.
create or replace function staging.to_date(v text) returns date
language plpgsql immutable as $$
begin
    if v is null or trim(v) !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
        return null;
    end if;
    return trim(v)::date;
exception when others then
    return null;
end
$$;

create or replace function staging.to_ts(v text) returns timestamp
language plpgsql immutable as $$
begin
    if v is null or trim(v) !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}$' then
        return null;
    end if;
    return trim(v)::timestamp;
exception when others then
    return null;
end
$$;

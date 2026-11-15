-- Schemas and the audit trail. Every statement is idempotent.
--
--   staging  raw files landed as text, truncated and reloaded for each batch
--   dwh      the star schema: conformed dimensions and facts
--   mart     reporting views with business-friendly names, consumed by Power BI
--   audit    ETL runs, delivered files, rejected rows, reconciliation results

create schema if not exists staging;
create schema if not exists dwh;
create schema if not exists mart;
create schema if not exists audit;

-- One row per attempt to load one batch (one month of source files).
create table if not exists audit.etl_run (
    run_id          bigint generated always as identity primary key,
    batch_id        varchar(7)   not null,               -- yyyy-mm
    status          varchar(10)  not null check (status in ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at      timestamptz  not null default now(),
    finished_at     timestamptz,
    duration_ms     bigint,
    error           text
);

-- At most one successful load per batch: this is what makes the pipeline incremental.
create unique index if not exists etl_run_one_success_per_batch
    on audit.etl_run (batch_id) where status = 'SUCCESS';

create table if not exists audit.etl_file (
    run_id          bigint       not null references audit.etl_run (run_id) on delete cascade,
    file_name       varchar(60)  not null,
    row_count       bigint       not null,
    sha256          char(64)     not null,
    primary key (run_id, file_name)
);

-- Quarantine. A rejected row is kept verbatim with the rule that rejected it.
create table if not exists audit.rejected_rows (
    run_id          bigint       not null references audit.etl_run (run_id) on delete cascade,
    batch_id        varchar(7)   not null,
    entity          varchar(30)  not null,
    rule_name       varchar(60)  not null,
    reason          varchar(200) not null,
    row_number      bigint       not null,
    record          jsonb        not null
);
create index if not exists rejected_rows_batch on audit.rejected_rows (batch_id, entity, rule_name);

-- Source-to-target control checks, written by every run.
create table if not exists audit.reconciliation (
    run_id          bigint        not null references audit.etl_run (run_id) on delete cascade,
    batch_id        varchar(7)    not null,
    entity          varchar(30)   not null,
    check_name      varchar(60)   not null,
    source_value    numeric(20,2) not null,
    target_value    numeric(20,2) not null,
    difference      numeric(20,2) generated always as (target_value - source_value) stored,
    status          varchar(4)    not null check (status in ('PASS', 'FAIL'))
);
create index if not exists reconciliation_batch on audit.reconciliation (batch_id, entity);

"""Validate: apply the configured rules to a staged file and quarantine what fails.

Rules live in config/validation_rules.yaml, not in code. Adding a rule is a configuration
change, and the rule's name is what appears in the quarantine and on the data quality page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import psycopg
import yaml

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Rule:
    name: str
    reason: str
    when: str


@dataclass(frozen=True)
class DuplicateRule:
    name: str
    reason: str
    key: str
    keep: str


@dataclass(frozen=True)
class EntityRules:
    entity: str
    rules: list[Rule] = field(default_factory=list)
    duplicates: DuplicateRule | None = None


class RuleConfigError(ValueError):
    """The rules file is not usable."""


def load_rules(path: Path) -> dict[str, EntityRules]:
    return parse_rules(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def parse_rules(document: dict) -> dict[str, EntityRules]:
    """Turn the YAML document into rule objects, refusing anything malformed."""
    parsed: dict[str, EntityRules] = {}
    for entity, body in document.items():
        if not _IDENTIFIER.match(entity):
            raise RuleConfigError(f"'{entity}' is not a valid entity name")
        seen: set[str] = set()
        rules = []
        for item in (body or {}).get("rules", []):
            missing = {"name", "reason", "when"} - set(item)
            if missing:
                raise RuleConfigError(f"{entity}: a rule is missing {sorted(missing)}")
            if not _IDENTIFIER.match(item["name"]):
                raise RuleConfigError(f"{entity}: '{item['name']}' is not a valid rule name")
            if item["name"] in seen:
                raise RuleConfigError(f"{entity}: rule '{item['name']}' is defined twice")
            seen.add(item["name"])
            rules.append(Rule(item["name"], item["reason"], " ".join(str(item["when"]).split())))
        duplicates = None
        if (body or {}).get("duplicates"):
            item = body["duplicates"]
            missing = {"name", "reason", "key", "keep"} - set(item)
            if missing:
                raise RuleConfigError(f"{entity}: the duplicates rule is missing {sorted(missing)}")
            if item["name"] in seen:
                raise RuleConfigError(f"{entity}: rule '{item['name']}' is defined twice")
            duplicates = DuplicateRule(item["name"], item["reason"], item["key"], item["keep"])
        parsed[entity] = EntityRules(entity, rules, duplicates)
    return parsed


def rule_sql(entity: str, rule: Rule) -> str:
    """The statement that marks the rows a rule rejects. Rows already rejected are left alone."""
    predicate = rule.when.replace(":batch_end", "cast(%(batch_end)s as date)")
    return (f"update staging.{entity}_work set rule = %(rule_name)s, rule_reason = %(reason)s "
            f"where rule is null and ({predicate})")


def duplicate_sql(entity: str, rule: DuplicateRule) -> str:
    """The statement that rejects all but the preferred row of each group sharing a key."""
    return f"""
        update staging.{entity}_work w set rule = %(rule_name)s, rule_reason = %(reason)s
        from (
            select row_number,
                   row_number() over (partition by {rule.key} order by {rule.keep}) as position
            from staging.{entity}_work
            where rule is null
        ) ranked
        where ranked.row_number = w.row_number and ranked.position > 1"""


def validate(conn: psycopg.Connection, rules: EntityRules, run_id: int, batch_id: str,
             batch_end: date) -> dict[str, int]:
    """Apply an entity's rules in order. Returns the number of rows each rule rejected."""
    entity = rules.entity
    conn.execute(f"drop table if exists staging.{entity}_work")
    conn.execute(f"create unlogged table staging.{entity}_work as "
                 f"select s.*, null::text as rule, null::text as rule_reason from staging.{entity} s")
    conn.execute(f"create index on staging.{entity}_work (row_number)")

    rejected: dict[str, int] = {}
    for rule in rules.rules:
        params = {"rule_name": rule.name, "reason": rule.reason, "batch_end": batch_end}
        if "%(batch_end)s" not in rule_sql(entity, rule):
            del params["batch_end"]
        count = conn.execute(rule_sql(entity, rule), params).rowcount
        if count:
            rejected[rule.name] = count
    if rules.duplicates:
        count = conn.execute(duplicate_sql(entity, rules.duplicates),
                             {"rule_name": rules.duplicates.name, "reason": rules.duplicates.reason}).rowcount
        if count:
            rejected[rules.duplicates.name] = count

    # The quarantined record is the source row exactly as delivered.
    conn.execute(
        f"""insert into audit.rejected_rows (run_id, batch_id, entity, rule_name, reason, row_number, record)
            select %(run_id)s, %(batch_id)s, %(entity)s, w.rule, w.rule_reason, w.row_number,
                   to_jsonb(w) - 'rule' - 'rule_reason' - 'row_number'
            from staging.{entity}_work w
            where w.rule is not null""",
        {"run_id": run_id, "batch_id": batch_id, "entity": entity},
    )
    return rejected

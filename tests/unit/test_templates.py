from pathlib import Path

import pytest
from jinja2 import UndefinedError

from src.db.templates import PostgresNumericDialect, SqlTemplateError, SqlTemplates


def make(tmp_path: Path, name: str, body: str) -> SqlTemplates:
    path = tmp_path / f"{name}.sql.j2"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return SqlTemplates(tmp_path, PostgresNumericDialect())


def test_bind_produces_numbered_placeholders_and_args(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "SELECT * FROM t WHERE a = {{ a | bind }} AND b = {{ b | bind }}")
    rendered = t.render("q", a=1, b="x")
    assert rendered.sql == "SELECT * FROM t WHERE a = $1 AND b = $2"
    assert rendered.args == (1, "x")


def test_each_bind_gets_its_own_placeholder(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "{{ a | bind }} {{ a | bind }}")
    rendered = t.render("q", a=5)
    assert rendered.sql == "$1 $2"
    assert rendered.args == (5, 5)


def test_injection_payload_stays_a_bound_value(tmp_path: Path) -> None:
    payload = "1; DROP TABLE channels; --"
    t = make(tmp_path, "q", "SELECT {{ v | bind }}")
    rendered = t.render("q", v=payload)
    assert rendered.sql == "SELECT $1"
    assert rendered.args == (payload,)


def test_ident_quotes_valid_names(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "SELECT 1 FROM {{ table | ident }}")
    assert t.render("q", table="schema_migrations").sql == 'SELECT 1 FROM "schema_migrations"'


@pytest.mark.parametrize("bad", ["x; DROP TABLE y", 'a"b', "1abc", "", "a.b", 42])
def test_ident_rejects_unsafe_names(tmp_path: Path, bad: object) -> None:
    t = make(tmp_path, "q", "SELECT 1 FROM {{ table | ident }}")
    with pytest.raises(SqlTemplateError):
        t.render("q", table=bad)


def test_raw_output_without_filter_is_forbidden(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "SELECT {{ v }}")
    with pytest.raises(SqlTemplateError):
        t.render("q", v=1)


def test_undefined_parameter_is_an_error(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "SELECT {{ missing | bind }}")
    with pytest.raises(UndefinedError):
        t.render("q")


def test_conditional_blocks_renumber_placeholders(tmp_path: Path) -> None:
    body = "WHERE a = {{ a | bind }}\n{% if b is not none %}\nAND b = {{ b | bind }}\n{% endif %}"
    t = make(tmp_path, "q", body)
    assert t.render("q", a=1, b=None).args == (1,)
    with_b = t.render("q", a=1, b=2)
    assert "AND b = $2" in with_b.sql
    assert with_b.args == (1, 2)


def test_reserved_parameter_name(tmp_path: Path) -> None:
    t = make(tmp_path, "q", "SELECT 1")
    with pytest.raises(SqlTemplateError):
        t.render("q", __sql_binds__=[])


def test_templates_in_subfolders(tmp_path: Path) -> None:
    t = make(tmp_path, "domain/q", "SELECT {{ v | bind }}")
    assert t.render("domain/q", v=1).sql == "SELECT $1"

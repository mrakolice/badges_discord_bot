import re
from pathlib import Path

import pytest
from jinja2 import meta

from src.db import SQL_ROOT, SqlTemplates
from src.db.migrations import MIGRATIONS_DIR
from src.db.queries import ALL_QUERY_GROUPS
from src.db.templates import TEMPLATE_SUFFIX

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
QUERIES_MODULE = SRC_ROOT / "db" / "queries.py"
TEMPLATE_NAME_LITERAL = re.compile(r"""["'][a-z_]+/[a-z0-9_]+["']""")
TEMPLATE_NAMES = sorted(
    p.relative_to(SQL_ROOT).as_posix().removesuffix(TEMPLATE_SUFFIX)
    for p in SQL_ROOT.rglob("*" + TEMPLATE_SUFFIX)
)
INLINE_SQL = re.compile(
    r"\b(SELECT\s|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+(TABLE|INDEX|UNIQUE)|DROP\s+(TABLE|SCHEMA|INDEX)|ALTER\s+TABLE|TRUNCATE\s)"
)


def test_templates_are_grouped_into_domain_folders() -> None:
    assert TEMPLATE_NAMES
    assert all("/" in name for name in TEMPLATE_NAMES)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_every_template_renders_with_matching_placeholders(
    templates: SqlTemplates, name: str
) -> None:
    source = templates.source(name)
    variables = meta.find_undeclared_variables(templates._env.parse(source))
    rendered = templates.render(name, **dict.fromkeys(variables, "x"))
    placeholders = {int(n) for n in re.findall(r"\$(\d+)", rendered.sql)}
    assert placeholders == set(range(1, len(rendered.args) + 1))
    assert "?" not in rendered.sql


def test_query_enums_match_template_files() -> None:
    members = [str(m) for group in ALL_QUERY_GROUPS for m in group]
    assert len(members) == len(set(members)), "duplicate template name in queries.py"
    files = {n for n in TEMPLATE_NAMES if not n.startswith(MIGRATIONS_DIR + "/")}
    assert set(members) == files


def test_query_groups_are_named_after_their_folder() -> None:
    for group in ALL_QUERY_GROUPS:
        folders = {m.value.split("/")[0] for m in group}
        assert len(folders) == 1, f"{group.__name__} spans several folders: {folders}"


@pytest.mark.parametrize("path", sorted(SRC_ROOT.rglob("*.py")), ids=lambda p: p.name)
def test_no_inline_sql_in_python_sources(path: Path) -> None:
    assert not INLINE_SQL.search(path.read_text()), f"SQL found in {path}; move it to sql/"


@pytest.mark.parametrize(
    "path",
    sorted(p for p in SRC_ROOT.rglob("*.py") if p != QUERIES_MODULE),
    ids=lambda p: p.name,
)
def test_template_names_only_via_query_enums(path: Path) -> None:
    found = TEMPLATE_NAME_LITERAL.findall(path.read_text())
    assert not found, f"{path}: use enums from src/db/queries.py instead of {found}"

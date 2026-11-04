import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined, pass_context
from jinja2.runtime import Context

TEMPLATE_SUFFIX = ".sql.j2"
_BINDS_KEY = "__sql_binds__"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SqlTemplateError(ValueError):
    pass


class SafeSql(str):
    """Fragment produced by the `bind`/`ident` filters; the only thing allowed into output."""


@dataclass(frozen=True, slots=True)
class RenderedQuery:
    sql: str
    args: tuple[Any, ...]


class Dialect(ABC):
    @abstractmethod
    def placeholder(self, position: int) -> str:
        """Placeholder for the bound argument at 1-based `position`."""

    @abstractmethod
    def quote_ident(self, name: str) -> str: ...


class PostgresNumericDialect(Dialect):
    def placeholder(self, position: int) -> str:
        return f"${position}"

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'


class SqlTemplates:
    def __init__(self, root: Path, dialect: Dialect) -> None:
        self.root = root
        self.dialect = dialect
        self._env = Environment(
            loader=FileSystemLoader(root),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            finalize=_only_safe_sql,
        )
        self._env.filters["bind"] = self._bind
        self._env.filters["ident"] = self._ident

    def render(self, name: str, /, **params: Any) -> RenderedQuery:
        if _BINDS_KEY in params:
            raise SqlTemplateError(f"parameter name {_BINDS_KEY!r} is reserved")
        binds: list[Any] = []
        template = self._env.get_template(name + TEMPLATE_SUFFIX)
        sql = template.render(**params, **{_BINDS_KEY: binds})
        return RenderedQuery(sql=sql.strip(), args=tuple(binds))

    def source(self, name: str) -> str:
        return (self.root / (name + TEMPLATE_SUFFIX)).read_text(encoding="utf-8")

    @pass_context
    def _bind(self, ctx: Context, value: Any) -> SafeSql:
        if isinstance(value, Undefined):
            str(value)  # StrictUndefined raises UndefinedError here
        binds: list[Any] = ctx[_BINDS_KEY]
        binds.append(value)
        return SafeSql(self.dialect.placeholder(len(binds)))

    def _ident(self, name: Any) -> SafeSql:
        if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
            raise SqlTemplateError(f"invalid SQL identifier: {name!r}")
        return SafeSql(self.dialect.quote_ident(name))


def _only_safe_sql(value: Any) -> Any:
    if isinstance(value, SafeSql):
        return value
    raise SqlTemplateError(f"raw value {value!r} in SQL template; use the `bind` or `ident` filter")

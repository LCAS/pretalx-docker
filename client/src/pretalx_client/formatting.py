"""Output helpers for the CLI: pretty JSON or a simple rich table."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from rich.console import Console
from rich.table import Table

console = Console()


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def print_table(rows: list[dict], columns: Optional[Iterable[str]] = None) -> None:
    if not rows:
        console.print("[dim]No results.[/dim]")
        return
    resolved_columns = list(columns) if columns else list(rows[0].keys())
    table = Table(show_lines=False)
    for column in resolved_columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(_stringify(row.get(column)) for column in resolved_columns))
    console.print(table)


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return "" if value is None else str(value)


def print_result(data: Any, fmt: str, columns: Optional[Iterable[str]] = None) -> None:
    """Print a single object or a list of objects, in the requested format."""
    if fmt == "json":
        print_json(data)
        return
    if isinstance(data, list):
        print_table(data, columns=columns)
    else:
        print_table([data], columns=columns)

"""
CLI interactivo del SQL Analyst Agent.

Uso:
    python -m src.main                 # usa store.db
    python -m src.main --db otra.db    # otra base de datos
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .agent import SQLAgent
from .database import Database
from .llm import LLMError, get_client

console = Console()

DEFAULT_DB = Path(__file__).parent.parent / "store.db"


def render_result(result) -> None:
    # Mensaje que no era una consulta de datos: solo la respuesta.
    if not result.is_query:
        console.print(Panel(result.explanation, border_style="yellow"))
        return

    console.print(Panel(Syntax(result.sql, "sql", theme="monokai", word_wrap=True),
                        title="SQL generado", border_style="cyan"))

    if result.columns:
        table = Table(show_header=True, header_style="bold magenta")
        for col in result.columns:
            table.add_column(str(col))
        for row in result.rows[:50]:
            table.add_row(*[str(v) for v in row])
        console.print(table)
        if len(result.rows) > 50:
            console.print(f"[dim]… {len(result.rows) - 50} filas más[/dim]")
    else:
        console.print("[dim](sin filas)[/dim]")

    console.print(Panel(result.explanation, title="Explicación", border_style="green"))

    if result.attempts > 1:
        console.print(f"[yellow]Resuelto tras {result.attempts} intentos.[/yellow]")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQL Analyst Agent (CLI)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Ruta a la base SQLite")
    args = parser.parse_args()

    load_dotenv()

    try:
        db = Database(args.db)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    agent = SQLAgent(db, get_client())

    console.print(Panel.fit(
        "[bold]SQL Analyst Agent[/bold]\n"
        "Preguntá en lenguaje natural sobre la base de datos.\n"
        "Recuerda la conversación, así que podés hacer preguntas de seguimiento.\n"
        "[dim]Comandos: 'schema' (ver tablas) · 'reset' (olvidar) · 'salir'.[/dim]",
        border_style="blue",
    ))

    while True:
        try:
            question = console.input("\n[bold cyan]?[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n¡Hasta luego!")
            break

        if not question:
            continue
        if question.lower() in {"salir", "exit", "quit", "q"}:
            console.print("¡Hasta luego!")
            break
        if question.lower() == "schema":
            console.print(Panel(db.schema_description(), title="Esquema", border_style="cyan"))
            continue
        if question.lower() in {"reset", "olvidar", "nuevo"}:
            agent.reset()
            console.print("[dim]Memoria borrada. Empezamos una conversación nueva.[/dim]")
            continue

        try:
            with console.status("[dim]Pensando…[/dim]"):
                result = agent.ask(question)
        except LLMError as exc:
            console.print(f"[red]Error de LLM:[/red] {exc}")
            continue
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            continue

        render_result(result)


if __name__ == "__main__":
    main()

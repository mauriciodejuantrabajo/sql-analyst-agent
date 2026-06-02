"""
Acceso a la base de datos SQLite: inspección de esquema y ejecución segura.

La ejecución es de SOLO LECTURA por diseño: se rechaza cualquier sentencia que
no sea un único SELECT. Esto evita que el LLM (o un prompt malicioso) modifique
o borre datos.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# Palabras clave que nunca deben aparecer: cualquier cosa que escriba/altere.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """La consulta generada no es un SELECT de solo lectura."""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"No existe la base de datos en {self.path}. "
                "Ejecutá primero `python seed_db.py`."
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def schema_description(self) -> str:
        """Devuelve el esquema en texto plano para dárselo al LLM como contexto.

        Para columnas de texto con pocos valores distintos (p. ej. `status`),
        incluye esos valores entre paréntesis. Así el LLM filtra usando los
        valores reales ('completado') y no inventa variantes ('completados').
        """
        conn = self._connect()
        try:
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ]
            lines: list[str] = []
            for table in tables:
                cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
                col_defs = []
                for c in cols:
                    desc = f"{c['name']} {c['type']}"
                    values = self._distinct_values(conn, table, c["name"], c["type"])
                    if values:
                        desc += " valores posibles: " + ", ".join(
                            f"'{v}'" for v in values
                        )
                    col_defs.append(desc)
                lines.append(f"{table}(" + ", ".join(col_defs) + ")")
            return "\n".join(lines)
        finally:
            conn.close()

    def table_summaries(self) -> dict[str, list[str]]:
        """Devuelve {tabla: [columnas]} para describir la base en lenguaje natural."""
        conn = self._connect()
        try:
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ]
            return {
                t: [c["name"] for c in conn.execute(f"PRAGMA table_info({t})")]
                for t in tables
            }
        finally:
            conn.close()

    @staticmethod
    def _distinct_values(
        conn: sqlite3.Connection, table: str, column: str, col_type: str, max_values: int = 10
    ) -> list[str] | None:
        """Valores distintos de una columna de texto, solo si son pocos.

        Devuelve None si la columna no es de texto o tiene demasiada cardinalidad
        (un email o un nombre no aportan como enum; un `status` sí).
        """
        if "char" not in col_type.lower() and "text" not in col_type.lower():
            return None
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {max_values + 1}'
        ).fetchall()
        if len(rows) > max_values:
            return None  # demasiados valores: no es categórica
        return [str(r[0]) for r in rows]

    @staticmethod
    def assert_safe(sql: str) -> None:
        """Lanza UnsafeQueryError si `sql` no es un único SELECT de solo lectura."""
        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            raise UnsafeQueryError("La consulta está vacía.")
        # Un solo statement: no permitir múltiples sentencias separadas por ';'.
        if ";" in stripped:
            raise UnsafeQueryError("Solo se permite una sentencia SQL.")
        if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
            raise UnsafeQueryError("Solo se permiten consultas SELECT.")
        if _FORBIDDEN.search(stripped):
            raise UnsafeQueryError(
                "La consulta contiene una operación de escritura no permitida."
            )

    def run_select(self, sql: str, limit: int = 100) -> tuple[list[str], list[tuple]]:
        """Valida y ejecuta un SELECT. Devuelve (columnas, filas)."""
        self.assert_safe(sql)
        conn = self._connect()
        try:
            cur = conn.execute(sql)
            rows = cur.fetchmany(limit)
            columns = [d[0] for d in cur.description] if cur.description else []
            return columns, [tuple(r) for r in rows]
        finally:
            conn.close()

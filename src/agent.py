"""
El agente SQL: convierte una pregunta en lenguaje natural en una respuesta.

Flujo:
    1. Lee el esquema de la base de datos.
    2. Le pide al LLM que genere un SELECT.
    3. Valida que sea de solo lectura y lo ejecuta.
    4. Si falla, reintenta pasándole el error al LLM (auto-corrección).
    5. Le pide al LLM que explique los resultados en lenguaje natural.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .database import Database, UnsafeQueryError
from .llm import LLMClient

SQL_SYSTEM_PROMPT = """\
Sos un analista de datos experto en SQL (dialecto SQLite). Dado el esquema de
una base de datos y una pregunta en lenguaje natural, devolvés UNA sola consulta
SQL de tipo SELECT que la responda.

Reglas estrictas:
- Devolvé SOLO la consulta SQL, sin explicaciones ni markdown.
- Usá únicamente SELECT (nunca INSERT/UPDATE/DELETE/DROP/etc.).
- Una sola sentencia, sin punto y coma final.
- Usá solo las tablas y columnas que aparecen en el esquema.
- Respetá TODOS los filtros que pide la pregunta (por ejemplo, si menciona
  "completados", agregá la condición de estado correspondiente).
- Incluí en el SELECT las columnas que hacen útil la respuesta: si la pregunta
  pide un ranking o un total, devolvé también ese valor calculado (con un alias),
  no solo el nombre.
"""

EXPLAIN_SYSTEM_PROMPT = """\
Sos un analista de datos. Te paso una pregunta del usuario, la consulta SQL que
se ejecutó y sus resultados. Explicá la respuesta en lenguaje natural, en
español, de forma breve y clara.

Reglas:
- No inventes datos que no estén en los resultados.
- Si los resultados están VACÍOS (0 filas), no afirmes que algo "no existe" en
  la base. Decí que la consulta no devolvió filas y sugerí que quizá el filtro
  no coincide con ningún dato; no saques conclusiones de negocio.
"""

CLASSIFY_SYSTEM_PROMPT = """\
Clasificás el mensaje del usuario en UNA de estas tres categorías. Respondé SOLO
con la palabra de la categoría, sin explicaciones.

- DATOS: pide un dato concreto que sale de consultar filas de la base.
  Ej: "cuántos clientes hay", "top 5 productos por ventas", "pedidos de Lima".

- META: pregunta sobre el agente o sobre QUÉ información hay disponible, no sobre
  un dato puntual. Incluye pedir ayuda o saber qué se puede preguntar.
  Ej: "qué tenés", "qué datos hay", "qué podés hacer", "ayuda", "cómo funcionás",
  "qué tablas hay", "sobre qué puedo preguntar".

- OTRO: saludos o charla que no se relacionan con la base.
  Ej: "hola", "gracias", "cómo estás", "contame un chiste".

Ante la duda entre DATOS y META: si el mensaje NO nombra una entidad concreta de
la base (cliente, pedido, producto, ciudad, etc.), es META.

Respondé exactamente: DATOS, META u OTRO.
"""


@dataclass
class AgentResult:
    question: str
    sql: str
    columns: list[str]
    rows: list[tuple]
    explanation: str
    attempts: int = 1
    errors: list[str] = field(default_factory=list)
    is_query: bool = True  # False si el mensaje no era una consulta de datos


def _extract_sql(text: str) -> str:
    """Quita fences de markdown y prefijos como 'SQL:' que el modelo a veces agrega."""
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    text = re.sub(r"^\s*sql\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip().rstrip(";").strip()


class SQLAgent:
    def __init__(self, db: Database, llm: LLMClient, max_retries: int = 2) -> None:
        self.db = db
        self.llm = llm
        self.max_retries = max_retries

    def _classify(self, question: str, schema: str) -> str:
        """Clasifica el mensaje en DATOS, META u OTRO."""
        prompt = f"Esquema:\n{schema}\n\nMensaje: {question}"
        answer = self.llm.chat(CLASSIFY_SYSTEM_PROMPT, prompt).strip().upper()
        for label in ("DATOS", "META", "OTRO"):
            if label in answer:
                return label
        return "DATOS"  # default conservador: intentar responder con datos

    def _describe_capabilities(self, schema: str) -> str:
        """Respuesta para preguntas META: qué hay en la base y qué se puede pedir."""
        tables = self.db.table_summaries()
        lines = ["Puedo consultar esta base de datos. Tablas disponibles:"]
        for name, cols in tables.items():
            lines.append(f"  • {name}: {', '.join(cols)}")
        lines.append(
            "\nProbá preguntas como: «¿cuántos clientes hay?», "
            "«top 5 productos más vendidos» o «pedidos completados de Lima». "
            "Escribí 'schema' para ver el detalle técnico."
        )
        return "\n".join(lines)

    def _generate_sql(self, question: str, schema: str, prev_error: str | None) -> str:
        prompt = f"Esquema:\n{schema}\n\nPregunta: {question}"
        if prev_error:
            prompt += (
                f"\n\nLa consulta anterior falló con este error:\n{prev_error}\n"
                "Corregila y devolvé solo el SELECT corregido."
            )
        raw = self.llm.chat(SQL_SYSTEM_PROMPT, prompt)
        return _extract_sql(raw)

    def _explain(self, question: str, sql: str, columns: list[str], rows: list[tuple]) -> str:
        preview = [dict(zip(columns, r)) for r in rows[:20]]
        prompt = (
            f"Pregunta: {question}\n\n"
            f"SQL ejecutado:\n{sql}\n\n"
            f"Resultados ({len(rows)} filas, muestra):\n{preview}"
        )
        return self.llm.chat(EXPLAIN_SYSTEM_PROMPT, prompt)

    def ask(self, question: str) -> AgentResult:
        schema = self.db.schema_description()

        # No fabriques SQL salvo que el mensaje pida un dato concreto.
        category = self._classify(question, schema)
        if category == "META":
            return AgentResult(
                question=question, sql="", columns=[], rows=[],
                explanation=self._describe_capabilities(schema),
                is_query=False,
            )
        if category == "OTRO":
            return AgentResult(
                question=question, sql="", columns=[], rows=[],
                explanation=(
                    "¡Hola! Soy un agente que responde preguntas sobre una base de "
                    "datos. Preguntame por ejemplo «¿cuántos clientes hay?» o "
                    "«top 5 productos más vendidos». Escribí 'schema' para ver las tablas."
                ),
                is_query=False,
            )

        errors: list[str] = []
        prev_error: str | None = None

        for attempt in range(1, self.max_retries + 2):  # 1 intento + reintentos
            sql = self._generate_sql(question, schema, prev_error)
            try:
                columns, rows = self.db.run_select(sql)
            except (UnsafeQueryError, Exception) as exc:  # noqa: BLE001
                prev_error = str(exc)
                errors.append(f"Intento {attempt}: {prev_error}")
                continue

            explanation = self._explain(question, sql, columns, rows)
            return AgentResult(
                question=question,
                sql=sql,
                columns=columns,
                rows=rows,
                explanation=explanation,
                attempts=attempt,
                errors=errors,
            )

        # Se agotaron los reintentos.
        raise RuntimeError(
            "No se pudo generar una consulta válida tras "
            f"{self.max_retries + 1} intentos.\n" + "\n".join(errors)
        )

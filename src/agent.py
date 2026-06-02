"""
El agente SQL: convierte una pregunta en lenguaje natural en una respuesta.

Flujo:
    1. Lee el esquema de la base de datos.
    2. Clasifica el mensaje (DATOS / META / OTRO) usando el historial como contexto.
    3. Le pide al LLM que genere un SELECT (con el historial para seguimientos).
    4. Valida que sea de solo lectura y lo ejecuta.
    5. Si falla, reintenta pasándole el error al LLM (auto-corrección).
    6. Le pide al LLM que explique los resultados en lenguaje natural.
    7. Guarda el turno (pregunta + SQL) en memoria para preguntas de seguimiento.
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
- Si te paso el HISTORIAL de la conversación, usalo para resolver preguntas de
  seguimiento. Frases como "y en otro lugar", "ahora los cancelados" o "¿y el
  año pasado?" se interpretan tomando la consulta anterior y cambiando solo el
  filtro que el usuario menciona. Por ejemplo, si antes filtró city = 'Lima' y
  ahora pide "otro lugar que no sea lima", usá city != 'Lima'.
"""

EXPLAIN_SYSTEM_PROMPT = """\
Sos un analista de datos. Te paso una pregunta del usuario, la consulta SQL que
se ejecutó y sus resultados. Explicá la respuesta en lenguaje natural, en
español, de forma breve y clara.

Reglas:
- No inventes datos que no estén en los resultados.
- Respondé directo a lo que se preguntó. Si el resultado es un único número
  (un conteo o un total), decí ese número de forma clara y breve; no comentes
  que "hay un solo resultado" ni que "falta información".
- Si los resultados están VACÍOS (0 filas), no afirmes que algo "no existe" en
  la base. Decí que la consulta no devolvió filas y sugerí que quizá el filtro
  no coincide con ningún dato; no saques conclusiones de negocio.
"""

REWRITE_SYSTEM_PROMPT = """\
Reescribís el último mensaje del usuario como una pregunta COMPLETA y
auto-contenida, resolviendo cualquier referencia al contexto de la conversación.

Te paso el historial (preguntas previas y el SQL que se ejecutó) y el mensaje
nuevo. Devolvé SOLO la pregunta reescrita, sin explicaciones.

Reglas:
- Si el mensaje ya es auto-contenido, devolvelo tal cual.
- Resolvé referencias elípticas usando el historial. Ejemplos (si antes se
  preguntó "cuántos clientes hay en Lima"):
    "y los que no hay ahi"      -> "¿cuántos clientes hay que NO sean de Lima?"
    "y cuantos no hay en lima"  -> "¿cuántos clientes hay que NO sean de Lima?"
    "y en Madrid?"              -> "¿cuántos clientes hay en Madrid?"
    "ahora los cancelados"      -> "¿cuántos clientes con pedidos cancelados hay?"
- Si el mensaje es un saludo o charla ("hola", "gracias"), devolvelo tal cual.
- No inventes filtros que el usuario no pidió.
"""

CLASSIFY_SYSTEM_PROMPT = """\
Clasificás el mensaje del usuario en UNA de estas tres categorías. Respondé SOLO
con la palabra de la categoría, sin explicaciones.

- DATOS: el usuario pide consultar, contar, listar, filtrar o calcular algo sobre
  las filas (clientes, pedidos, productos, etc.). Es DATOS aunque el filtro use un
  valor que quizá no exista en la base: igual hay que ejecutar la consulta.
  Ejemplos:
    "cuántos clientes hay" -> DATOS
    "top 5 productos por ventas" -> DATOS
    "pedidos de Lima" -> DATOS
    "arma la consulta de ordenes y customers de Londres, cuántos hay" -> DATOS
    "dame los clientes de Marte" -> DATOS (se ejecuta aunque no haya resultados)

- META: pregunta sobre el agente o sobre QUÉ información existe en general, sin
  pedir un dato puntual. Incluye pedir ayuda o saber qué se puede preguntar.
  Ejemplos:
    "qué tenés" -> META
    "qué datos hay" -> META
    "qué podés hacer" -> META
    "ayuda" -> META
    "qué tablas hay" -> META

- OTRO: saludos o charla sin relación con la base.
  Ejemplos:
    "hola" -> OTRO
    "gracias" -> OTRO
    "contame un chiste" -> OTRO

Regla clave: si el mensaje pide un dato, conteo, listado o filtro (verbos como
"cuántos", "dame", "mostrame", "listá", "arma la consulta", "top"), es DATOS,
sin importar si el valor del filtro existe o no.

Si te paso el HISTORIAL de la conversación, tenelo en cuenta: una pregunta de
seguimiento sobre datos sigue siendo DATOS aunque sea corta o use referencias
("y en otro lugar", "ahora los cancelados", "¿y los de Madrid?").

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
    def __init__(
        self, db: Database, llm: LLMClient, max_retries: int = 2, memory_turns: int = 4
    ) -> None:
        self.db = db
        self.llm = llm
        self.max_retries = max_retries
        self.memory_turns = memory_turns
        # Historial de turnos de datos: (pregunta, sql). Solo se guardan los
        # que generaron una consulta válida, que es lo útil como contexto.
        self._history: list[tuple[str, str]] = []

    def reset(self) -> None:
        """Olvida la conversación previa."""
        self._history.clear()

    def _history_block(self) -> str:
        """Formatea los últimos turnos para inyectar como contexto, o '' si no hay."""
        if not self._history:
            return ""
        recent = self._history[-self.memory_turns:]
        lines = ["HISTORIAL de la conversación (más reciente al final):"]
        for q, sql in recent:
            lines.append(f"- Usuario: {q}")
            lines.append(f"  SQL: {sql}")
        return "\n".join(lines) + "\n\n"

    def _rewrite(self, question: str) -> str:
        """Reescribe un seguimiento como pregunta auto-contenida usando el historial.

        Sin historial no hay nada que resolver: se devuelve el mensaje tal cual.
        """
        if not self._history:
            return question
        prompt = f"{self._history_block()}Mensaje nuevo: {question}"
        rewritten = self.llm.chat(REWRITE_SYSTEM_PROMPT, prompt).strip()
        return rewritten or question

    def _classify(self, question: str, schema: str) -> str:
        """Clasifica una pregunta YA auto-contenida en DATOS, META u OTRO.

        No recibe historial a propósito: opera sobre la versión reescrita, que es
        mucho más fácil de clasificar de forma fiable con un modelo chico.
        """
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
        # `question` ya viene reescrita y auto-contenida (sin referencias al contexto).
        prompt = f"Esquema:\n{schema}\n\nPregunta: {question}"
        if prev_error:
            prompt += (
                f"\n\nLa consulta anterior falló con este error:\n{prev_error}\n"
                "Corregila y devolvé solo el SELECT corregido."
            )
        raw = self.llm.chat(SQL_SYSTEM_PROMPT, prompt)
        return _extract_sql(raw)

    def _explain(self, question: str, sql: str, columns: list[str], rows: list[tuple]) -> str:
        # Caso escalar (un solo valor, típico de COUNT/SUM): presentarlo directo
        # para que el modelo no se enrede con "hay una sola fila".
        if len(rows) == 1 and len(rows[0]) == 1:
            results_block = f"El valor calculado es: {rows[0][0]}"
        else:
            preview = [dict(zip(columns, r)) for r in rows[:20]]
            results_block = f"Resultados ({len(rows)} filas, muestra):\n{preview}"
        prompt = (
            f"Pregunta: {question}\n\n"
            f"SQL ejecutado:\n{sql}\n\n"
            f"{results_block}"
        )
        return self.llm.chat(EXPLAIN_SYSTEM_PROMPT, prompt)

    def ask(self, question: str) -> AgentResult:
        schema = self.db.schema_description()

        # 1. Reescribir el seguimiento como pregunta auto-contenida. A partir de
        #    acá se trabaja con `query`, que ya no tiene referencias al contexto.
        query = self._rewrite(question)

        # 2. Clasificar la versión reescrita (más fácil y fiable).
        category = self._classify(query, schema)
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
            sql = self._generate_sql(query, schema, prev_error)
            try:
                columns, rows = self.db.run_select(sql)
            except (UnsafeQueryError, Exception) as exc:  # noqa: BLE001
                prev_error = str(exc)
                errors.append(f"Intento {attempt}: {prev_error}")
                continue

            explanation = self._explain(query, sql, columns, rows)
            # Guardar la versión reescrita: encadena bien los próximos seguimientos.
            self._history.append((query, sql))
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

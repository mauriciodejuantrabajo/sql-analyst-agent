# 🧠 SQL Analyst Agent

Haz una pregunta en **lenguaje natural** y el agente genera el SQL, lo ejecuta
sobre una base SQLite y explica el resultado — todo corriendo **localmente con
Ollama**, sin APIs de pago.

```
?  ¿Cuáles son los 5 clientes que más gastaron en pedidos completados?

  SQL generado
  SELECT customers.name, SUM(order_items.unit_price * order_items.quantity) AS total_spent
  FROM customers
  JOIN orders ON customers.id = orders.customer_id
  JOIN order_items ON orders.id = order_items.order_id
  WHERE orders.status = 'completado'
  GROUP BY customers.name
  ORDER BY total_spent DESC
  LIMIT 5

  ┌──────────────────┬─────────────┐
  │ name             │ total_spent │
  ├──────────────────┼─────────────┤
  │ Rocío Rodríguez  │ 9249.67     │
  │ Facundo Martínez │ 7944.37     │
  │ ...              │ ...         │
  └──────────────────┴─────────────┘

  Explicación
  Estos son los 5 clientes que más gastaron en pedidos completados...
```

## El problema

Consultar una base de datos requiere saber SQL y conocer el esquema. La mayoría
de la gente de negocio sabe **qué quiere preguntar**, pero no **cómo escribirlo**.

## La solución

Un agente *text-to-SQL* que:

1. **Inspecciona el esquema** de la base solo (tablas y columnas).
2. **Genera el SQL** con un LLM, usando el esquema como contexto.
3. **Valida que sea seguro** — rechaza cualquier cosa que no sea un `SELECT` de
   solo lectura (nada de `DROP`, `DELETE`, `UPDATE`, múltiples sentencias, etc.).
4. **Ejecuta** la consulta sobre SQLite.
5. **Se auto-corrige**: si el SQL falla, le pasa el error al LLM y reintenta.
6. **Explica** el resultado en lenguaje natural.
7. **Recuerda la conversación**: las preguntas de seguimiento ("¿y en otro lugar?",
   "ahora los cancelados") se resuelven usando el contexto de los turnos previos.

## Arquitectura

```
src/
├── llm.py        Capa de LLM. Hoy Ollama; diseñada para sumar otro backend sin tocar el resto.
├── database.py   Inspección de esquema + ejecución SOLO LECTURA (validación de seguridad).
├── agent.py      Orquesta: clasifica → SQL → ejecuta → (reintenta) → explica → recuerda.
└── main.py       CLI interactivo (rich).
seed_db.py        Genera store.db con datos de ejemplo (e-commerce).
```

## Cómo correrlo

Requisitos: Python 3.10+ y [Ollama](https://ollama.com/) corriendo localmente.

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Descargar un modelo (rápido y suficiente para text-to-SQL)
ollama pull llama3.2

# 3. Generar la base de datos de ejemplo
python seed_db.py

# 4. Lanzar el agente
python -m src.main
```

Dentro del CLI:
- Escribe cualquier pregunta en lenguaje natural.
- Puedes hacer preguntas de seguimiento: el agente recuerda la conversación.
  Ej: *"¿cuántos clientes hay en Lima?"* → *"¿y en otro lugar que no sea Lima?"*
- `schema` muestra las tablas disponibles.
- `reset` olvida la conversación y empieza de cero.
- `salir` para terminar.

## Configuración

Copia `.env.example` a `.env` para ajustar el modelo o el host:

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

> **Nota sobre el modelo:** `llama3.2` (3B) es rápido pero a veces simplifica
> consultas complejas. Para mayor precisión puedes usar un modelo más grande
> (p. ej. `OLLAMA_MODEL=llama3.1:8b`).

## Base de datos de ejemplo

`seed_db.py` crea una tienda e-commerce con datos sintéticos (semilla fija, así
que es reproducible):

| Tabla | Descripción |
|-------|-------------|
| `customers` | 50 clientes (nombre, email, ciudad, alta) |
| `products` | 12 productos (nombre, categoría, precio) |
| `orders` | ~150 pedidos (cliente, fecha, estado) |
| `order_items` | ~370 líneas de pedido (producto, cantidad, precio) |

## Seguridad

La ejecución es **de solo lectura por diseño**. `database.assert_safe()` rechaza
cualquier consulta que no sea un único `SELECT`/`WITH`, bloqueando palabras clave
de escritura y múltiples sentencias. El LLM no puede modificar ni borrar datos.

## Próximos pasos

- [ ] Soporte para un backend LLM adicional vía API (la capa `llm.py` ya está preparada).
- [ ] Interfaz Streamlit.
- [ ] Soporte para PostgreSQL/MySQL.
- [ ] Suite de evals (preguntas → SQL esperado) para medir precisión por modelo.

## Licencia

[MIT](LICENSE) © Mauricio De Juan

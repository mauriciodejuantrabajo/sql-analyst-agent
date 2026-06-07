> **Idioma / Language:** **English** · [Español](README.es.md)

# 🧠 SQL Analyst Agent

Ask a question in **natural language** and the agent generates the SQL, runs it
against a SQLite database and explains the result — all running **locally with
Ollama**, no paid APIs.

```
?  Who are the top 5 customers by spend on completed orders?

  Generated SQL
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

  Explanation
  These are the 5 customers who spent the most on completed orders...
```

## The problem

Querying a database requires knowing SQL and the schema. Most business people
know **what they want to ask**, but not **how to write it**.

## The solution

A *text-to-SQL* agent that:

1. **Inspects the database schema** on its own (tables and columns).
2. **Generates the SQL** with an LLM, using the schema as context.
3. **Validates that it's safe** — rejects anything that isn't a read-only
   `SELECT` (no `DROP`, `DELETE`, `UPDATE`, multiple statements, etc.).
4. **Executes** the query against SQLite.
5. **Self-corrects**: if the SQL fails, it passes the error back to the LLM and
   retries.
6. **Explains** the result in natural language.
7. **Remembers the conversation**: follow-up questions ("and somewhere else?",
   "now the cancelled ones") are resolved using the context of previous turns.

## Architecture

```
src/
├── llm.py        LLM layer. Today Ollama; designed to add another backend without touching the rest.
├── database.py   Schema inspection + READ-ONLY execution (safety validation).
├── agent.py      Orchestrates: classify → SQL → execute → (retry) → explain → remember.
└── main.py       Interactive CLI (rich).
seed_db.py        Generates store.db with sample data (e-commerce).
```

## How to run it

Requirements: Python 3.10+ and [Ollama](https://ollama.com/) running locally.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Pull a model (fast and good enough for text-to-SQL)
ollama pull llama3.2

# 3. Generate the sample database
python seed_db.py

# 4. Launch the agent
python -m src.main
```

Inside the CLI:
- Type any natural-language question.
- You can ask follow-up questions: the agent remembers the conversation.
  e.g. *"how many customers are in Lima?"* → *"and somewhere other than Lima?"*
- `schema` shows the available tables.
- `reset` forgets the conversation and starts over.
- `salir` to exit.

## Configuration

Copy `.env.example` to `.env` to adjust the model or the host:

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

> **Note on the model:** `llama3.2` (3B) is fast but sometimes oversimplifies
> complex queries. For more accuracy you can use a larger model
> (e.g. `OLLAMA_MODEL=llama3.1:8b`).

## Sample database

`seed_db.py` creates an e-commerce store with synthetic data (fixed seed, so it's
reproducible):

| Table | Description |
|-------|-------------|
| `customers` | 50 customers (name, email, city, signup date) |
| `products` | 12 products (name, category, price) |
| `orders` | ~150 orders (customer, date, status) |
| `order_items` | ~370 order lines (product, quantity, price) |

## Security

Execution is **read-only by design**. `database.assert_safe()` rejects any query
that isn't a single `SELECT`/`WITH`, blocking write keywords and multiple
statements. The LLM cannot modify or delete data.

## Next steps

- [ ] Support for an additional LLM backend via API (the `llm.py` layer is ready).
- [ ] Streamlit interface.
- [ ] PostgreSQL/MySQL support.
- [ ] Eval suite (questions → expected SQL) to measure accuracy per model.

## License

[MIT](LICENSE) © Mauricio De Juan

"""
Genera una base de datos SQLite de ejemplo (tienda e-commerce).

Crea `store.db` con tablas realistas y datos sintéticos para que el agente
tenga algo interesante que consultar. Es determinista (semilla fija) para que
los resultados sean reproducibles.

Uso:
    python seed_db.py
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"

random.seed(42)  # reproducible

FIRST_NAMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elena", "Facundo", "Gabriela",
    "Hugo", "Irene", "Joaquín", "Karina", "Lucas", "Marina", "Nicolás",
    "Olivia", "Pablo", "Rocío", "Santiago", "Tamara", "Valentín",
]
LAST_NAMES = [
    "Gómez", "Pérez", "Rodríguez", "Fernández", "López", "Martínez",
    "Sánchez", "Romero", "Díaz", "Torres", "Ruiz", "Acosta",
]
CITIES = ["Montevideo", "Buenos Aires", "Santiago", "Lima", "Bogotá", "Madrid"]

PRODUCTS = [
    ("Teclado mecánico", "Periféricos", 89.99),
    ("Mouse inalámbrico", "Periféricos", 39.50),
    ("Monitor 27\" 4K", "Monitores", 349.00),
    ("Notebook 14\"", "Computadoras", 1199.00),
    ("Auriculares Bluetooth", "Audio", 129.90),
    ("Webcam 1080p", "Periféricos", 59.99),
    ("Disco SSD 1TB", "Almacenamiento", 99.00),
    ("Memoria RAM 16GB", "Componentes", 74.90),
    ("Silla ergonómica", "Mobiliario", 259.00),
    ("Hub USB-C", "Accesorios", 34.99),
    ("Micrófono USB", "Audio", 119.00),
    ("Tablet 10\"", "Computadoras", 299.00),
]


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript(
        """
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            id          INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            email       TEXT    NOT NULL UNIQUE,
            city        TEXT    NOT NULL,
            created_at  DATE    NOT NULL
        );

        CREATE TABLE products (
            id       INTEGER PRIMARY KEY,
            name     TEXT    NOT NULL,
            category TEXT    NOT NULL,
            price    REAL    NOT NULL
        );

        CREATE TABLE orders (
            id          INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date  DATE    NOT NULL,
            status      TEXT    NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE order_items (
            id         INTEGER PRIMARY KEY,
            order_id   INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity   INTEGER NOT NULL,
            unit_price REAL    NOT NULL,
            FOREIGN KEY (order_id)   REFERENCES orders(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        """
    )


def seed(cur: sqlite3.Cursor) -> None:
    # --- customers ---
    customers = []
    for i in range(1, 51):
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        email = f"user{i}@example.com"
        city = random.choice(CITIES)
        created = date(2023, 1, 1) + timedelta(days=random.randint(0, 700))
        customers.append((i, name, email, city, created.isoformat()))
    cur.executemany(
        "INSERT INTO customers (id, name, email, city, created_at) VALUES (?,?,?,?,?)",
        customers,
    )

    # --- products ---
    products = [
        (i, name, cat, price) for i, (name, cat, price) in enumerate(PRODUCTS, start=1)
    ]
    cur.executemany(
        "INSERT INTO products (id, name, category, price) VALUES (?,?,?,?)",
        products,
    )

    # --- orders + order_items ---
    statuses = ["completado", "completado", "completado", "pendiente", "cancelado"]
    order_id = 0
    item_id = 0
    orders, items = [], []
    for customer_id in range(1, 51):
        for _ in range(random.randint(0, 6)):  # algunos clientes no compran
            order_id += 1
            odate = date(2024, 1, 1) + timedelta(days=random.randint(0, 500))
            status = random.choice(statuses)
            orders.append((order_id, customer_id, odate.isoformat(), status))
            for _ in range(random.randint(1, 4)):
                item_id += 1
                pid, pname, pcat, pprice = random.choice(products)
                qty = random.randint(1, 3)
                items.append((item_id, order_id, pid, qty, pprice))

    cur.executemany(
        "INSERT INTO orders (id, customer_id, order_date, status) VALUES (?,?,?,?)",
        orders,
    )
    cur.executemany(
        "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) "
        "VALUES (?,?,?,?,?)",
        items,
    )


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        create_schema(cur)
        seed(cur)
        conn.commit()

        counts = {
            t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("customers", "products", "orders", "order_items")
        }
    finally:
        conn.close()

    print(f"Base de datos creada en: {DB_PATH}")
    for table, n in counts.items():
        print(f"  {table:12} {n:>5} filas")


if __name__ == "__main__":
    main()

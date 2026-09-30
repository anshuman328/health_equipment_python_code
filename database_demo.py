import sqlite3

connection = sqlite3.connect("local.db")

connection.execute("DROP TABLE IF EXISTS products")
connection.execute(
    """
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        in_stock INTEGER NOT NULL DEFAULT 1
    )
    """
)

connection.executemany(
    "INSERT INTO products (name, price, in_stock) VALUES (?, ?, ?)",
    [
        ("Notebook", 4.99, 1),
        ("Pen", 1.50, 1),
        ("Backpack", 29.99, 0),
    ],
)

connection.execute(
    "UPDATE products SET price = ? WHERE name = ?",
    (5.49, "Notebook"),
)

connection.execute("DELETE FROM products WHERE name = ?", ("Backpack",))
connection.commit()

products = connection.execute("SELECT * FROM products").fetchall()
print(products)

connection.close()

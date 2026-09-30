-- SQLite sample

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    in_stock INTEGER NOT NULL DEFAULT 1
);

INSERT INTO products (name, price, in_stock)
VALUES
    ('Notebook', 4.99, 1),
    ('Pen', 1.50, 1),
    ('Backpack', 29.99, 0);

-- Read products that are in stock
SELECT id, name, price
FROM products
WHERE in_stock = 1
ORDER BY price;

-- Update a product
UPDATE products
SET price = 5.49
WHERE name = 'Notebook';

-- Delete a product
DELETE FROM products
WHERE name = 'Backpack';

-- Show the final data
SELECT * FROM products;

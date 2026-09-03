"""
Database Initialization Script for Web Scraper.
Imports product names and links from amazon_products.csv into SQLite (products.db).
"""

import sqlite3
import pandas as pd
import numpy as np
import os

CSV_FILE = "amazon_products.csv"
DB_FILE = "products.db"
TABLE_NAME = "products"

def create_database():
    """Reads amazon_products.csv and populates SQLite database products.db."""
    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found!")
        return

    print(f"Reading dataset from {CSV_FILE}...")
    df = pd.read_csv(CSV_FILE)

    # 1. Remove unwanted columns
    columns_to_drop = ["actual_price", "main_category", "sub_category"]
    df = df.drop(columns=[col for col in columns_to_drop if col in df.columns])

    # 2. Rename discount_price to price
    if "discount_price" in df.columns:
        df.rename(columns={"discount_price": "price"}, inplace=True)

    # 3. Set ratings, no_of_ratings, and image columns to NULL (None)
    for col in ["image", "ratings", "no_of_ratings"]:
        if col in df.columns:
            df[col] = None

    # 4. Set 'link' column to NULL (None) for even product indices (odd products have direct link, even products don't)
    if "link" in df.columns:
        # 1-indexed product numbers: odd products (1, 3, 5...) keep direct link, even products (2, 4, 6...) have link set to None
        even_mask = (df.index + 1) % 2 == 0
        df.loc[even_mask, "link"] = None

    print(f"Connecting to SQLite database: {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Drop table if exists to ensure clean schema recreation
    cursor.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")

    # Create products table schema
    cursor.execute(f"""
        CREATE TABLE {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            link TEXT,
            price TEXT,
            image TEXT,
            ratings TEXT,
            no_of_ratings TEXT
        )
    """)

    # Create indexes for fast lookup by product name and link
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_product_name ON {TABLE_NAME} (name);")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_product_link ON {TABLE_NAME} (link);")

    # Insert CSV records into database
    print(f"Inserting {len(df)} records into '{TABLE_NAME}' table...")
    df.to_sql(TABLE_NAME, conn, if_exists='append', index=False)

    conn.commit()

    # Verify count and null link count
    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    row_count = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE link IS NULL")
    null_link_count = cursor.fetchone()[0]

    print(f"\n[SUCCESS] SQLite database '{DB_FILE}' created successfully with {row_count} records.")
    print(f"  -> Links with NULL values: {null_link_count} ({null_link_count/row_count*100:.1f}%)")

    conn.close()

if __name__ == "__main__":
    create_database()

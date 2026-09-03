"""
Database Manager Module for Tor Web Scraper.
Provides SQLite lookup and insertion helpers for product data.
"""

import sqlite3
import os
from typing import Optional, Dict, Any

DB_FILE = "products.db"
TABLE_NAME = "products"

def get_db_connection() -> sqlite3.Connection:
    """Returns a connection to the SQLite products database with Row factory."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def find_product_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Search database for exact product name match."""
    if not os.path.exists(DB_FILE):
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {TABLE_NAME} WHERE name = ? LIMIT 1", (name.strip(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def find_product_by_link(link: str) -> Optional[Dict[str, Any]]:
    """Search database for product matching link URL."""
    if not os.path.exists(DB_FILE):
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {TABLE_NAME} WHERE link = ? LIMIT 1", (link.strip(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_product_link(name: str, new_link: Optional[str]) -> None:
    """Explicitly update or nullify the link column for a product by name."""
    if not os.path.exists(DB_FILE):
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE {TABLE_NAME} SET link = ? WHERE name = ?", (new_link, name))
        conn.commit()

def save_scraped_product(
    name: str,
    link: Optional[str] = None,
    price: Optional[str] = None,
    ratings: Optional[str] = None,
    no_of_ratings: Optional[str] = None,
    image: Optional[str] = None
) -> None:
    """Update existing product record or insert newly scraped product into database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT id FROM {TABLE_NAME} WHERE name = ? OR (link IS NOT NULL AND link = ?) LIMIT 1", (name, link))
        row = cursor.fetchone()

        if row:
            cursor.execute(f"""
                UPDATE {TABLE_NAME}
                SET link = COALESCE(?, link),
                    price = COALESCE(?, price),
                    ratings = COALESCE(?, ratings),
                    no_of_ratings = COALESCE(?, no_of_ratings),
                    image = COALESCE(?, image)
                WHERE id = ?
            """, (link, price, ratings, no_of_ratings, image, row["id"]))
        else:
            cursor.execute(f"""
                INSERT INTO {TABLE_NAME} (name, link, price, ratings, no_of_ratings, image)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, link, price, ratings, no_of_ratings, image))
        conn.commit()

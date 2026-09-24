"""SQLite-backed store for the hardware inventory module.

Pure-python, zero-dependency. A single table for items with a few indexed
columns; builds and the network map are derived from items for now.
"""
import json
import os
import sqlite3
import threading
import time

__all__ = ["Store"]

DEFAULT_DB = os.environ.get("INVENTORY_DB", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    serial TEXT,
    qty INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
"""


class Store:
    def __init__(self, path=None):
        self.path = path or DEFAULT_DB
        if not self.path:
            raise ValueError("inventory store path required "
                             "(set INVENTORY_DB or pass path)")
        self.lock = threading.Lock()
        with self.lock:
            with sqlite3.connect(self.path) as db:
                db.executescript(SCHEMA)

    def list_items(self, page=1, per_page=50, category=None, status=None):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            where, args = [], []
            if category:
                where.append("category = ?"); args.append(category)
            if status:
                where.append("status = ?"); args.append(status)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            total = db.execute(f"SELECT COUNT(*) FROM items {clause}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM items {clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                args + [per_page, (page - 1) * per_page]).fetchall()
            return {"items": [dict(r) for r in rows], "total": total}

    def create_item(self, data):
        now = time.time()
        with self.lock, sqlite3.connect(self.path) as db:
            cur = db.execute(
                "INSERT INTO items (name, category, status, serial, qty, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data.get("name", ""), data.get("category", "other"),
                 data.get("status", "active"), data.get("serial"),
                 data.get("qty", 1), now, now))
            item = {"id": cur.lastrowid, **data}
        return item

    def get_item(self, item_id):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def update_item(self, item_id, data):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return None
            merged = {**dict(row), **{k: v for k, v in data.items() if v is not None}}
            merged["updated_at"] = time.time()
            db.execute(
                "UPDATE items SET name=?, category=?, status=?, serial=?, qty=?, updated_at=? WHERE id=?",
                (merged["name"], merged["category"], merged["status"],
                 merged["serial"], merged["qty"], merged["updated_at"], item_id))
            return merged

    def delete_item(self, item_id):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT status FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return False
            db.execute("UPDATE items SET status='retired', updated_at=? WHERE id=?",
                       (time.time(), item_id))
            return True

    def stats(self):
        with self.lock, sqlite3.connect(self.path) as db:
            total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            retired = db.execute("SELECT COUNT(*) FROM items WHERE status='retired'").fetchone()[0]
            low_stock = db.execute(
                "SELECT COUNT(*) FROM items WHERE qty <= 0").fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM items WHERE updated_at > ?",
                (time.time() - 7 * 86400,)).fetchone()[0]
            return {
                "total_items": total,
                "warranty_expiring": 0,
                "low_stock": low_stock,
                "recent_changes": recent,
            }

    def list_builds(self):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in
                    db.execute("SELECT * FROM builds ORDER BY created_at DESC")]

    def create_build(self, data):
        with self.lock, sqlite3.connect(self.path) as db:
            cur = db.execute("INSERT INTO builds (name, created_at) VALUES (?, ?)",
                             (data.get("name", "Unnamed build"), time.time()))
            return {"id": cur.lastrowid, "name": data.get("name", "Unnamed build")}

    def get_build(self, build_id):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM builds WHERE id = ?", (build_id,)).fetchone()
            return dict(row) if row else None
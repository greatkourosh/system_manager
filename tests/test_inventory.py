"""Tests for the inventory module's SQLite-backed store and API."""
import os
import tempfile
import unittest

from system_manager import create_app
from system_manager.inventory import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="sm-inv-"), "inv.db")
        self.store = store.Store(self.path)

    def test_create_and_list(self):
        self.store.create_item({"name": "RTX 4090", "category": "pc_component", "qty": 1})
        result = self.store.list_items()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["name"], "RTX 4090")

    def test_soft_delete(self):
        item = self.store.create_item({"name": "DDR5", "category": "pc_component"})
        self.assertTrue(self.store.delete_item(item["id"]))
        fetched = self.store.get_item(item["id"])
        self.assertEqual(fetched["status"], "retired")

    def test_stats(self):
        self.store.create_item({"name": "A", "category": "pc_component", "qty": 0})
        stats = self.store.stats()
        self.assertEqual(stats["total_items"], 1)
        self.assertEqual(stats["low_stock"], 1)


class InventoryApiTests(unittest.TestCase):
    def setUp(self):
        data_dir = tempfile.mkdtemp(prefix="sm-inv-api-")
        self.app = create_app({"DISABLE_AUTH": 1,
                               "INVENTORY_DATA_DIR": os.path.join(data_dir, "inv.db")})
        self.client = self.app.test_client()

    def test_create_and_fetch_item(self):
        created = self.client.post("/inventory/items", json={
            "name": "Monitor", "category": "pc_component", "qty": 2,
        })
        self.assertEqual(created.status_code, 201)
        item_id = created.get_json()["id"]
        listed = self.client.get("/inventory/items",
                                 headers={"Accept": "application/json"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["total"], 1)

    def test_dashboard_render(self):
        response = self.client.get("/inventory/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Total Items", response.data)


if __name__ == "__main__":
    unittest.main()
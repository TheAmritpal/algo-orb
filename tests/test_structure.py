import importlib
import unittest


class StructureTests(unittest.TestCase):
    def test_application_boundaries_import(self):
        for name in (
            "app.core.config",
            "app.repositories.database",
            "app.repositories.tokens",
            "app.routers.pages",
            "app.routers.upstox",
            "app.services.upstox",
        ):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))

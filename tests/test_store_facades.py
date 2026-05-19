import importlib
import unittest

from ragbaz_frog import store


class StoreFacades(unittest.TestCase):
    MODULES = ("store_federation", "store_db", "store_provider")

    def test_facades_import_clean_and_mirror_store(self):
        for modname in self.MODULES:
            mod = importlib.import_module(f"ragbaz_frog.{modname}")
            self.assertTrue(mod.__all__, f"{modname}.__all__ empty")
            for name in mod.__all__:
                self.assertTrue(hasattr(store, name),
                                f"{modname} exports unknown store.{name}")
                self.assertIs(getattr(mod, name), getattr(store, name),
                              f"{modname}.{name} != store.{name}")

    def test_facades_partition_without_overlap(self):
        seen = {}
        for modname in self.MODULES:
            mod = importlib.import_module(f"ragbaz_frog.{modname}")
            for name in mod.__all__:
                self.assertNotIn(
                    name, seen,
                    f"{name} in both {seen.get(name)} and {modname}")
                seen[name] = modname
        # the facades should name a meaningful chunk of the surface
        self.assertGreaterEqual(len(seen), 20)


if __name__ == "__main__":
    unittest.main()

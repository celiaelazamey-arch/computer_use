import unittest

from cela_sh.memory.metadata import correction_first, metadata_matches, normalize_memory_type, normalize_tags


class MemoryMetadataTests(unittest.TestCase):
    def test_tags_are_casefolded_unique_sorted_and_unicode_safe(self):
        self.assertEqual(normalize_tags(["Testing", "database", "testing", "اختبار"]), ["database", "testing", "اختبار"])

    def test_invalid_tags_and_types_are_rejected(self):
        invalid_tags = ("testing", [""], ["two words"], ["x" * 65], [1], ["a"] * 21)
        for tags in invalid_tags:
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                normalize_tags(tags)
        for value in ("Correction", "other", 3):
            with self.subTest(memory_type=value), self.assertRaises(ValueError):
                normalize_memory_type(value)

    def test_metadata_filter_requires_all_tags(self):
        metadata = {"type": "correction", "tags": ["testing", "database"]}
        self.assertTrue(metadata_matches(metadata, tags=["TESTING", "database"], memory_type="correction"))
        self.assertFalse(metadata_matches(metadata, tags=["testing", "deployment"]))
        self.assertFalse(metadata_matches(metadata, memory_type="note"))

    def test_corrections_are_prioritized_then_sorted_by_similarity(self):
        memories = [
            {"metadata": {"type": "note"}, "score": 0.99, "content": "general"},
            {"metadata": {"type": "correction"}, "score": 0.2, "content": "correction low"},
            {"metadata": {"type": "correction"}, "score": 0.8, "content": "correction high"},
        ]
        self.assertEqual(
            [memory["content"] for memory in correction_first(memories)],
            ["correction high", "correction low", "general"],
        )


if __name__ == "__main__":
    unittest.main()

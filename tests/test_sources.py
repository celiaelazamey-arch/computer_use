import hashlib
import unittest

from cela_sh.memory.sources import content_hash, normalize_source_path, validate_source_replacement


class SourceIdentityTests(unittest.TestCase):
    def test_normalizes_relative_posix_paths_and_rejects_host_paths(self):
        self.assertEqual(normalize_source_path("docs//guide.md"), "docs/guide.md")
        for path in ("/etc/passwd", "../outside.md", "docs/../../outside.md", "C:/private/guide.md", "docs\\guide.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_source_path(path)

    def test_content_hash_is_sha256_of_the_stored_utf8_chunk(self):
        content = "مرحبا\nCELA"
        self.assertEqual(content_hash(content), hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_replacement_records_must_match_source_and_chunk_identity(self):
        source_path = "docs/guide.md"
        source_hash = hashlib.sha256(b"file bytes").hexdigest()
        content = "Deployment instructions"
        metadata = {
            "scope": "project",
            "type": "note",
            "tags": ["deployment"],
            "source_path": source_path,
            "source_hash": source_hash,
            "content_hash": content_hash(content),
            "imported_at": "2026-10-12T12:00:00+00:00",
            "chunk_index": 1,
            "chunk_count": 1,
        }

        session, path, digest, chunks = validate_source_replacement(
            "project-a", source_path, source_hash, [(content, metadata)]
        )

        self.assertEqual((session, path, digest), ("project-a", source_path, source_hash))
        self.assertEqual(chunks, [(content, metadata)])
        bad_metadata = {**metadata, "source_path": "../guide.md"}
        with self.assertRaises(ValueError):
            validate_source_replacement("project-a", source_path, source_hash, [(content, bad_metadata)])


if __name__ == "__main__":
    unittest.main()

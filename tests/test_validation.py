from __future__ import annotations

import unittest

from agent_memory_kit.errors import PublicError
from agent_memory_kit.memory import MemoryService, canonical_hash, validate_event
from support import CONTENT, DatabaseTestCase, make_event, raw_event


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_equivalent_timestamps_have_identical_hash(self):
        first = make_event()
        second = make_event(request_id=first["id"], occurred_at="2026-11-07T12:00:00+03:00")
        self.assertEqual(first["payload_hash"], second["payload_hash"])
        self.assertEqual(first["payload_hash"], canonical_hash({**first, "recorded_at": "2026-11-07T10:00:00Z"}))

    def test_validation_matrix(self):
        cases = [
            {"occurred_at": "2026-11-07"},
            {"title": "x" * 161},
            {"source_refs": []},
            {"details": "api_key=abcdefghijklmnopqrstuvwxyz123456"},
            {"project": True},
            {"event_status": "accepted"},
            {"application_id": "bad application"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(PublicError):
                make_event(**changes)
        event = make_event()
        with self.assertRaises(PublicError):
            make_event(request_id=event["id"], supersedes_id=event["id"])

    async def test_shared_safe_is_mandatory_before_any_write(self):
        service = MemoryService(None, None)
        with self.assertRaises(PublicError):
            await service.record(None, raw_event(make_event(), shared_safe=False))


if __name__ == "__main__":
    unittest.main()

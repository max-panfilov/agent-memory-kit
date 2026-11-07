from __future__ import annotations

import unittest

from agent_memory_kit.errors import PublicError
from agent_memory_kit.memory import MemoryService
from support import DatabaseTestCase, make_event, make_principal, raw_event


class ViewTests(DatabaseTestCase):
    async def test_confirmed_correction_filters_active_views_but_not_timeline(self):
        service = MemoryService(self.repository, self.snapshot)
        old = make_event(title="Старое решение")
        correction = make_event(
            event_type="correction",
            event_status="confirmed",
            supersedes_id=old["id"],
            title="Исправленное решение",
        )
        await self.repository.append(old, "demo-writer")
        await self.repository.append(correction, "demo-writer")
        brief = await service.brief(self.principal, 1)
        self.assertEqual([row["id"] for row in brief["recent_events"]], [correction["id"]])
        timeline = await service.timeline(self.principal, 1)
        self.assertEqual(len(timeline["events"]), 2)
        result = await service.search(self.principal, 1, "решение")
        self.assertEqual([item["event"]["id"] for item in result["results"]], [correction["id"]])
        self.assertEqual(result["ranking"], "canonical-bm25-v1")

    async def test_proposed_correction_does_not_hide_original(self):
        service = MemoryService(self.repository, self.snapshot)
        old = make_event()
        proposed = make_event(event_type="correction", event_status="proposed", supersedes_id=old["id"])
        await self.repository.append(old, "demo-writer")
        await self.repository.append(proposed, "demo-writer")
        self.assertEqual(len((await service.brief(self.principal, 1))["recent_events"]), 2)

    async def test_non_latin_search_and_cross_scope_denial(self):
        service = MemoryService(self.repository, self.snapshot)
        event = make_event(title="Решение о миграции памяти", summary="Единая история для агентов")
        await service.record(self.principal, raw_event(event))
        result = await service.search(self.principal, 1, "миграции")
        self.assertEqual(result["results"][0]["event"]["id"], event["id"])
        with self.assertRaises(PublicError):
            await service.read(make_principal(project=2, applications=("other-app",)), 1, event["id"])

    async def test_pagination_is_deterministic(self):
        service = MemoryService(self.repository, self.snapshot)
        events = [make_event(occurred_at="2026-11-07T09:00:00Z") for _ in range(3)]
        for event in events:
            await self.repository.append(event, "demo-writer")
        first = await service.timeline(self.principal, 1, limit=2)
        second = await service.timeline(self.principal, 1, cursor=first["next_cursor"], limit=2)
        ids = [row["id"] for row in first["events"] + second["events"]]
        expected = [row["id"] for row in sorted(events, key=lambda row: (row["occurred_at"], row["id"]), reverse=True)]
        self.assertEqual(ids, expected)
        self.assertIsNone(second["next_cursor"])


if __name__ == "__main__":
    unittest.main()

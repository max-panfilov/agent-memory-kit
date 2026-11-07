from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import asyncpg

from agent_memory_kit.errors import PublicError
from support import DatabaseTestCase, make_event


class RepositoryTests(DatabaseTestCase):
    async def test_replay_conflict_and_parallel_append(self):
        event = make_event()
        first, second = await asyncio.gather(
            self.repository.append(event, "demo-writer"),
            self.repository.append(event, "demo-writer"),
        )
        self.assertEqual({first["replayed"], second["replayed"]}, {False, True})
        self.assertEqual(await self.repository.count(), 1)
        with self.assertRaisesRegex(PublicError, "CONFLICT"):
            await self.repository.append(make_event(request_id=event["id"], title="Changed"), "demo-writer")

    async def test_foreign_supersession_is_refused(self):
        other = make_event(project=2, application_id="other-app")
        await self.repository.append(other, "other-writer")
        with self.assertRaisesRegex(PublicError, "SUPERSEDED"):
            await self.repository.append(make_event(supersedes_id=other["id"]), "demo-writer")

    async def test_commit_ack_loss_reconciles_by_original_uuid(self):
        event = make_event()
        original_commit = asyncpg.transaction.Transaction._Transaction__commit
        calls = 0

        async def losing_commit(connection):
            nonlocal calls
            result = await original_commit(connection)
            calls += 1
            raise asyncpg.exceptions.InterfaceError("injected lost commit acknowledgement")

        with patch("asyncpg.transaction.Transaction._Transaction__commit", new=losing_commit):
            with self.assertRaisesRegex(PublicError, "OUTCOME_UNKNOWN"):
                await self.repository.append(event, "demo-writer")
        self.assertEqual(calls, 1)
        saved = await self.repository.read(1, event["id"])
        self.assertEqual(saved["payload_hash"], event["payload_hash"])
        replay = await self.repository.append(event, "demo-writer")
        self.assertTrue(replay["replayed"])
        self.assertEqual(await self.repository.count(), 1)


if __name__ == "__main__":
    unittest.main()

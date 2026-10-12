"""Grammar regression tests; local MySQL only, all LLM/S2S calls mocked."""
import asyncio
import json
import os
import secrets
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

from app import grammar, grammar_sessions as lifecycle
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo, db_session
from app.db.engine import init_db, db_engine
from app.db.models import DailyUsage, MytrackerEvent, MytrackerInstall, Session, User
from app.main import app

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
SAFE_DB = bool(TEST_URL and make_url(TEST_URL).host in {"localhost", "127.0.0.1"}
               and (make_url(TEST_URL).database or "").endswith("_test"))
EXERCISES = [{"id": str(i), "type": "mcq", "category": "tense", "prompt": "She ___ a teacher.",
              "choices": ["am", "is", "are", "be"], "correct": "is", "explanation": "Правило."} for i in range(1, 5)]


@unittest.skipUnless(SAFE_DB, "TEST_DATABASE_URL must point to a local *_test database")
class GrammarLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(settings, "DATABASE_URL", TEST_URL),
                        patch.object(settings, "AUTH_JWT_SECRET", secrets.token_hex(32)),
                        patch.object(settings, "ADMIN_TOKEN", secrets.token_hex(32)),
                        patch.object(settings, "FREE_PERIOD", True),
                        patch.object(settings, "MYTRACKER_APP_ID", "123"),
                        patch.object(settings, "MYTRACKER_S2S_TOKEN", "synthetic-token"),
                        patch("app.mytracker._post", new_callable=AsyncMock, return_value=True),
                        patch.object(grammar, "_call_llm", new_callable=AsyncMock, return_value=json.dumps(EXERCISES))]
        for item in self.patches:
            self.llm = item.start()
        lifecycle.PREPARED.clear()
        init_db()
        self.uids = []
        async with db_session() as session:
            repo = Repo(session)
            for _ in range(2):
                user = await repo.create_native_user(email=f"grammar-test-{uuid4().hex}@example.invalid", password_hash="unused", first_name="Test")
                self.uids.append(int(user.id))
            from app.grammar_lessons import THEORY
            topics = list(await repo.list_grammar_topics())
            self.topic = next(t.key for t in topics if t.key in THEORY)
        self.uid, self.other_uid = self.uids
        self.headers = {"Authorization": f"Bearer {issue_jwt(self.uid)}", "Origin": "http://localhost:5173",
                        "X-MyTracker-Instance-ID": str(uuid4())}
        self.other_headers = {"Authorization": f"Bearer {issue_jwt(self.other_uid)}"}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test", trust_env=False)

    async def asyncTearDown(self):
        await self.client.aclose()
        lifecycle.PREPARED.clear()
        async with db_session() as session:
            for model, column in [(MytrackerEvent, MytrackerEvent.user_id), (MytrackerInstall, MytrackerInstall.user_id),
                                  (Session, Session.user_id), (User, User.id)]:
                await session.execute(delete(model).where(column.in_(self.uids)))
        await db_engine().dispose()
        for item in reversed(self.patches):
            item.stop()

    async def post(self, path, body, expected=200, headers=None):
        response = await self.client.post('/api/grammar' + path, json=body, headers=headers or self.headers)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def prepared(self, lesson=False):
        data = await self.post('/lesson/exercises' if lesson else '/generate',
                               {"topic_key": self.topic} if lesson else {"mode": "topic", "level": "A2", "category": "tense"})
        return data['session_id']

    async def started(self, lesson=False):
        key = await self.prepared(lesson)
        result = await self.post('/start', {"session_id": key})
        return key, result['session_id']

    async def rows(self):
        async with db_session() as session:
            return list((await session.scalars(select(Session).where(Session.user_id == self.uid).order_by(Session.id))).all())

    async def test_failed_generation_then_retry_creates_only_actual_practice(self):
        self.llm.side_effect = httpx.ReadTimeout('synthetic timeout')
        response = await self.client.post('/api/grammar/generate', headers=self.headers,
                                          json={"mode": "topic", "category": "tense"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:5173')
        self.assertEqual(await self.rows(), [])
        self.llm.side_effect = None
        key, sid = await self.started()
        await self.post('/finish', {"session_id": sid, "results": [], "duration_sec": 90})
        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].grammar_status, 'completed')
        self.assertEqual(rows[0].used_seconds, 90)
        self.assertIsNotNone(rows[0].ended_at)

    async def test_lesson_prefetch_and_cancel_do_not_create_or_consume_sessions(self):
        with patch.object(settings, 'FREE_PERIOD', False), patch.object(settings, 'FREE_TRIAL_DAYS', 0):
            key = await self.prepared(lesson=True)
            self.assertEqual(await self.rows(), [])
            async with db_session() as session:
                self.assertEqual(await Repo(session).count_sessions_today(self.uid, 'grammar'), 0)
            await self.post('/abandon', {"session_id": key})
            await self.post('/start', {"session_id": key}, expected=410)
            self.assertEqual(await self.rows(), [])

    async def test_concurrent_different_preparations_cannot_exceed_free_quota(self):
        with patch.object(settings, 'FREE_PERIOD', False), patch.object(settings, 'FREE_TRIAL_DAYS', 0):
            keys = [await self.prepared(), await self.prepared()]
            results = await asyncio.gather(*(self.client.post('/api/grammar/start', headers=self.headers,
                json={'session_id': key}) for key in keys))
            self.assertEqual(sorted(response.status_code for response in results), [200, 402])
            self.assertEqual(len(await self.rows()), 1)

    async def test_concurrent_activation_and_lost_reply_reuse_one_persisted_session(self):
        key = await self.prepared()
        starts = await asyncio.gather(*(self.post('/start', {"session_id": key}) for _ in range(4)))
        self.assertEqual(len({s['session_id'] for s in starts}), 1)
        lifecycle.PREPARED.clear()  # simulate the process losing all preparation memory
        resumed = await self.post('/start', {"session_id": key})
        self.assertEqual(resumed, starts[0])
        self.assertEqual(len(await self.rows()), 1)
        async with db_session() as session:
            events = list((await session.scalars(select(MytrackerEvent).where(MytrackerEvent.user_id == self.uid))).all())
        self.assertEqual(sum(e.payload.get('customEventName') == 'session_started' for e in events), 1)

    async def test_duplicate_finish_is_idempotent_across_restart(self):
        key, sid = await self.started(lesson=True)
        body = {"session_id": sid, "topic_key": self.topic, "correct": 3, "total": 4, "duration_sec": 90}
        results = await asyncio.gather(*(self.post('/lesson/finish', body) for _ in range(3)))
        lifecycle.PREPARED.clear()
        self.assertEqual(await self.post('/lesson/finish', body), results[0])
        self.assertTrue(all(result == results[0] for result in results))
        async with db_session() as session:
            progress = (await Repo(session).get_user_grammar_progress(self.uid))[self.topic]
            total = await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id == self.uid))
        self.assertEqual(progress['attempts'], 1)
        self.assertEqual(total, 90)

    async def test_failed_completion_rolls_back_progress_and_usage_then_retry_succeeds(self):
        _, sid = await self.started(lesson=True)
        body = {'session_id': sid, 'topic_key': self.topic, 'correct': 3, 'total': 4, 'duration_sec': 90}
        with patch.object(Repo, 'bump_streak', new_callable=AsyncMock, side_effect=RuntimeError('synthetic finish failure')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url='http://local.test', trust_env=False) as client:
                response = await client.post('/api/grammar/lesson/finish', headers=self.headers, json=body)
                self.assertEqual(response.status_code, 500)
        row = (await self.rows())[0]
        self.assertEqual(row.grammar_status, 'active')
        self.assertIsNone(row.ended_at)
        self.assertIsNone(row.grammar_result)
        async with db_session() as session:
            self.assertEqual(await Repo(session).get_user_grammar_progress(self.uid), {})
            self.assertIsNone(await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id == self.uid)))
        await self.post('/lesson/finish', body)
        await self.post('/lesson/finish', body)
        async with db_session() as session:
            self.assertEqual((await Repo(session).get_user_grammar_progress(self.uid))[self.topic]['attempts'], 1)
            self.assertEqual(await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id == self.uid)), 90)

    async def test_foreign_user_cannot_start_finish_touch_or_abandon_session(self):
        key, sid = await self.started()
        await self.post('/start', {"session_id": key}, expected=403, headers=self.other_headers)
        for path in ['/heartbeat', '/abandon', '/finish']:
            await self.post(path, {"session_id": sid, "results": [], "duration_sec": 90}, expected=404, headers=self.other_headers)
        row = (await self.rows())[0]
        self.assertIsNone(row.ended_at)
        self.assertEqual(row.grammar_status, 'active')

    async def test_idle_and_legacy_sessions_are_abandoned_without_fabricated_usage(self):
        key, sid = await self.started()
        await self.post('/heartbeat', {"session_id": sid, "duration_sec": 35})
        old = lifecycle.now() - timedelta(seconds=lifecycle.ACTIVITY_TTL + 10)
        async with db_session() as session:
            await session.execute(update(Session).where(Session.id == int(sid)).values(grammar_last_seen_at=old))
            session.add(Session(user_id=self.uid, mode='grammar', role='tense', level='A2', started_at=old, used_seconds=0))
        self.assertGreaterEqual(await lifecycle.close_inactive(), 2)
        rows = await self.rows()
        self.assertTrue(all(row.grammar_status == 'abandoned' and row.ended_at for row in rows))
        self.assertEqual([r.used_seconds for r in rows], [35, 0])
        async with db_session() as session:
            usage = await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id == self.uid))
        self.assertIsNone(usage)
        data = await self.client.get(f'/api/admin/users/{self.uid}/sessions', headers={'X-Admin-Token': settings.ADMIN_TOKEN})
        self.assertEqual(data.status_code, 200, data.text)
        self.assertTrue(all(r['status'] == 'abandoned' for r in data.json()['sessions']))

    async def test_completion_survives_lost_store_and_inactivity(self):
        key, sid = await self.started()
        lifecycle.PREPARED.clear()
        await self.post('/heartbeat', {"session_id": sid, "duration_sec": 40})
        async with db_session() as session:
            await session.execute(update(Session).where(Session.id == int(sid)).values(
                grammar_last_seen_at=lifecycle.now() - timedelta(seconds=lifecycle.ACTIVITY_TTL + 10)))
        await lifecycle.close_inactive()
        result = await self.post('/finish', {"session_id": sid, "results": [], "duration_sec": 90})
        self.assertEqual(await self.post('/finish', {"session_id": sid, "results": [], "duration_sec": 90}), result)
        async with db_session() as session:
            usage = await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id == self.uid))
        self.assertEqual(usage, 90)

    async def test_cancellation_racing_activation_never_leaves_active_session(self):
        key = await self.prepared()
        started, abandoned = await asyncio.gather(
            self.client.post('/api/grammar/start', headers=self.headers, json={'session_id': key}),
            self.client.post('/api/grammar/abandon', headers=self.headers, json={'session_id': key}))
        self.assertIn(started.status_code, [200, 410])
        self.assertEqual(abandoned.status_code, 200, abandoned.text)
        self.assertTrue(all(r.grammar_status == 'abandoned' for r in await self.rows()))
        await self.post('/start', {'session_id': key}, expected=410)

    async def test_preparation_expiration_has_no_db_or_quota_side_effects(self):
        key = await self.prepared()
        lifecycle.PREPARED[key].expires_at = 0
        await lifecycle.close_inactive()
        await self.post('/start', {'session_id': key}, expected=410)
        self.assertEqual(await self.rows(), [])

    async def test_previous_frontend_finishes_prepared_token_idempotently(self):
        key = await self.prepared()
        result = await self.post('/finish', {'session_id': key, 'results': [], 'duration_sec': 90})
        self.assertEqual(await self.post('/finish', {'session_id': key, 'results': [], 'duration_sec': 90}), result)
        self.assertEqual(len(await self.rows()), 1)

    async def test_start_requires_valid_authentication(self):
        key = await self.prepared()
        response = await self.client.post('/api/grammar/start', json={'session_id': key})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(await self.rows(), [])


if __name__ == '__main__':
    unittest.main()

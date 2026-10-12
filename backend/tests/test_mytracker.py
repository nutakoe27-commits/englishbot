"""Run integration tests with TEST_DATABASE_URL pointing to a local *_test DB.

MyTracker HTTP is mocked throughout; these tests never emit advertising events.
"""
import os
import secrets
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

from app import mytracker as mt
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo, db_session
from app.db.engine import db_engine, init_db
from app.db.models import MytrackerEvent, MytrackerInstall, Payment, Session, User

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
SAFE_DB = bool(TEST_URL and make_url(TEST_URL).host in {"localhost", "127.0.0.1"}
               and make_url(TEST_URL).database.endswith("_test"))


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_s2s_wire_format_and_errors(self):
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200 if len(requests) == 1 else 403, json={})
        original = httpx.AsyncClient
        def client(**kwargs):
            return original(**kwargs, transport=httpx.MockTransport(transport), trust_env=False)
        with patch.object(settings, "MYTRACKER_APP_ID", "123"), patch.object(settings, "MYTRACKER_S2S_TOKEN", "synthetic-test-token"), patch.object(httpx, "AsyncClient", side_effect=client):
            body = mt._base(7, str(uuid4()))
            self.assertTrue(await mt._post("registration", body))
            self.assertFalse(await mt._post("registration", body))
        self.assertEqual(requests[0].url.path, "/v1/registration")
        self.assertEqual(requests[0].url.params["idApp"], "123")
        self.assertEqual(requests[0].headers["Authorization"], "synthetic-test-token")
        self.assertEqual(__import__('json').loads(requests[0].content), body)

    async def test_instance_ids_and_disabled_configuration(self):
        self.assertTrue(mt.valid_instance_id(str(uuid4())))
        for bad in [None, "", "abcdefgh", str(uuid4()) + "\n", "fake-instance-id", "x" * 36]:
            self.assertFalse(mt.valid_instance_id(bad))
        for invalid in [None, "", "0", "not-an-id", "１２３"]:
            with patch.object(settings, "MYTRACKER_APP_ID", invalid):
                self.assertFalse(mt.enabled())
        with patch.object(settings, "MYTRACKER_APP_ID", "123"), patch.object(settings, "MYTRACKER_S2S_TOKEN", ""):
            self.assertEqual(await mt.deliver_pending(), 0)


@unittest.skipUnless(SAFE_DB, "TEST_DATABASE_URL must be a local database ending in _test")
class FunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(settings, "DATABASE_URL", TEST_URL),
                        patch.object(settings, "MYTRACKER_APP_ID", "123"),
                        patch.object(settings, "MYTRACKER_S2S_TOKEN", "synthetic-test-token"),
                        patch.object(settings, "AUTH_JWT_SECRET", secrets.token_hex(32)),
                        patch.object(settings, "ADMIN_TOKEN", secrets.token_hex(32))]
        for item in self.patches:
            item.start()
        init_db()
        self.iid = str(uuid4())
        async with db_session() as session:
            user = await Repo(session).create_native_user(email=f"mt-test-{uuid4().hex}@example.invalid", password_hash="unused-test-hash", first_name="Test")
            self.uid = int(user.id)
        self.post = patch.object(mt, "_post", new_callable=AsyncMock)
        self.sender = self.post.start()
        self.sender.return_value = True

    async def asyncTearDown(self):
        self.post.stop()
        async with db_session() as session:
            for model, column in [(MytrackerEvent, MytrackerEvent.user_id), (MytrackerInstall, MytrackerInstall.user_id), (Session, Session.user_id), (Payment, Payment.user_id), (User, User.id)]:
                await session.execute(delete(model).where(column == self.uid))
        await db_engine().dispose()
        for item in reversed(self.patches):
            item.stop()

    async def events(self):
        async with db_session() as session:
            return list((await session.scalars(select(MytrackerEvent).where(MytrackerEvent.user_id == self.uid).order_by(MytrackerEvent.id))).all())

    async def link(self, iid=None):
        async with db_session() as session:
            return await mt.link_in_session(session, user_id=self.uid, instance_id=iid or self.iid)

    async def test_registration_survives_missing_configuration_and_repeat_login(self):
        with patch.object(settings, "MYTRACKER_S2S_TOKEN", None):
            self.assertTrue(await self.link())
            self.assertEqual(await mt.deliver_pending(), 0)
        self.assertFalse(await self.link())
        rows = await self.events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].method, "registration")
        self.assertIsNone(rows[0].sent_at)
        self.assertEqual(await mt.deliver_pending(), 1)
        self.assertIsNotNone((await self.events())[0].sent_at)
        self.assertEqual(await mt.deliver_pending(), 0)
        self.assertEqual(self.sender.await_count, 1)

    async def test_existing_account_sends_login(self):
        async with db_session() as session:
            await session.execute(update(User).where(User.id == self.uid).values(created_at=mt._utcnow() - timedelta(days=5)))
        await self.link()
        self.assertEqual((await self.events())[0].method, "login")

    async def test_legacy_install_is_not_replayed_but_last_seen_is_refreshed(self):
        old = mt._utcnow() - timedelta(days=3)
        async with db_session() as session:
            session.add(MytrackerInstall(user_id=self.uid, instance_id=self.iid, created_at=old, last_seen_at=old))
        self.assertFalse(await self.link())
        self.assertEqual(await self.events(), [])
        async with db_session() as session:
            install = await session.get(MytrackerInstall, (self.uid, self.iid))
            self.assertGreater(install.last_seen_at, old)

    async def test_all_lesson_modes_use_current_device_and_browser_does_not(self):
        other_iid = str(uuid4())
        await self.link(other_iid)
        async with db_session() as session:
            repo = Repo(session)
            await repo.open_session(user_id=self.uid, mode="chat", level="A2", role="barista")
            for mode in ["voice", "chat", "grammar", "listening", "srs"]:
                await repo.open_session(user_id=self.uid, mode=mode, level="A2", role=None, mytracker_instance_id=self.iid)
        rows = await self.events()
        starts = [r for r in rows if r.payload.get("customEventName") == "session_started"]
        self.assertEqual(len(starts), 5)
        self.assertEqual({r.payload["customEventParams"]["mode"] for r in starts}, {"voice", "chat", "grammar", "listening", "srs"})
        for row in starts:
            self.assertEqual(row.payload["instanceId"], self.iid)
            self.assertEqual(row.payload["customUserId"], str(self.uid))
            self.assertTrue(all(isinstance(v, str) for v in row.payload["customEventParams"].values()))

    async def test_rest_header_context_and_diagnostic_authentication(self):
        from app.main import app
        headers = {"Authorization": f"Bearer {issue_jwt(self.uid)}", "X-MyTracker-Instance-ID": self.iid}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test", trust_env=False) as client:
            native = await client.post("/api/srs/session/start", headers=headers, json={})
            self.assertEqual(native.status_code, 200, native.text)
            browser = await client.post("/api/srs/session/start", headers={"Authorization": headers["Authorization"]}, json={})
            self.assertEqual(browser.status_code, 200, browser.text)
            denied = await client.get("/api/analytics/mt-status")
            self.assertEqual(denied.status_code, 401)
            state = await client.get("/api/analytics/mt-status", headers={"X-Admin-Token": settings.ADMIN_TOKEN})
            self.assertEqual(state.status_code, 200, state.text)
            self.assertTrue(state.json()["tracking_enabled"])
            self.assertGreaterEqual(state.json()["pending"], 2)
        starts = [r for r in await self.events() if r.payload.get("customEventName") == "session_started"]
        self.assertEqual(len(starts), 1)
        self.assertIsNone(mt.current_instance_id.get())

    async def create_payment(self):
        async with db_session() as session:
            payment = await Repo(session).create_pending_payment(user_id=self.uid, plan="monthly", amount_rub=999, days_granted=30, provider_payment_id=f"mt-test-{uuid4().hex}")
            return int(payment.id)

    async def test_payment_only_after_credit_and_callback_is_idempotent(self):
        await self.link()
        pid = await self.create_payment()
        self.assertEqual(len(await self.events()), 1)
        async with db_session() as session:
            self.assertTrue(await Repo(session).credit_subscription_for_payment(pid))
        rows = await self.events()
        self.assertEqual(len(rows), 3)
        revenue = next(r for r in rows if r.method == "customRevenue")
        paid = next(r for r in rows if r.payload.get("customEventName") == "subscription_paid")
        self.assertEqual(revenue.payload["idTransaction"], f"yk-{pid}")
        self.assertEqual(revenue.payload["total"], 999.0)
        self.assertEqual(revenue.payload["currency"], "RUB")
        self.assertEqual(paid.payload["customEventParams"]["amount_rub"], "999")
        async with db_session() as session:
            self.assertTrue(await Repo(session).credit_subscription_for_payment(pid))
        self.assertEqual(len(await self.events()), 3)

    async def test_payment_for_account_without_install_has_no_mobile_events(self):
        pid = await self.create_payment()
        async with db_session() as session:
            self.assertTrue(await Repo(session).credit_subscription_for_payment(pid))
        self.assertEqual(await self.events(), [])

    async def test_failed_sub_event_retries_without_resending_revenue(self):
        await self.link()
        pid = await self.create_payment()
        async with db_session() as session:
            await Repo(session).credit_subscription_for_payment(pid)
        async def send(method, payload):
            return payload.get("customEventName") != "subscription_paid"
        self.sender.side_effect = send
        self.assertEqual(await mt.deliver_pending(), 3)
        rows = await self.events()
        paid = next(r for r in rows if r.payload.get("customEventName") == "subscription_paid")
        self.assertIsNone(paid.sent_at)
        self.assertEqual(paid.attempts, 1)
        self.assertGreater(paid.next_attempt_at, mt._utcnow())
        async with db_session() as session:
            await session.execute(update(MytrackerEvent).where(MytrackerEvent.id == paid.id).values(next_attempt_at=mt._utcnow()))
        self.sender.side_effect = None
        self.assertEqual(await mt.deliver_pending(), 1)
        self.assertEqual(sum(call.args[0] == "customRevenue" for call in self.sender.await_args_list), 1)
        self.assertTrue(all(r.sent_at is not None for r in await self.events()))

    async def test_business_rollback_discards_link_and_event(self):
        async with db_session() as session:
            await Repo(session).open_session(user_id=self.uid, mode="chat", level=None, role=None, mytracker_instance_id=self.iid)
            await session.rollback()
        self.assertEqual(await self.events(), [])
        async with db_session() as session:
            self.assertIsNone(await session.get(MytrackerInstall, (self.uid, self.iid)))

    async def test_concurrent_logins_enqueue_only_one_registration(self):
        import asyncio
        await asyncio.gather(self.link(), self.link())
        self.assertEqual(len(await self.events()), 1)

    async def test_concurrent_delivery_workers_do_not_send_the_same_row(self):
        import asyncio
        await self.link()
        async def send(method, payload):
            await asyncio.sleep(0.02)
            return True
        self.sender.side_effect = send
        await asyncio.gather(mt.deliver_pending(limit=1), mt.deliver_pending(limit=1))
        self.assertEqual(self.sender.await_count, 1)
        self.assertIsNotNone((await self.events())[0].sent_at)

    async def test_application_lifespan_delivers_queued_events(self):
        import asyncio
        from app.main import app
        await self.link()
        async with app.router.lifespan_context(app):
            for _ in range(40):
                if (await self.events())[0].sent_at is not None:
                    break
                await asyncio.sleep(0.025)
            self.assertIsNotNone((await self.events())[0].sent_at)
        self.assertEqual(self.sender.await_count, 1)
        self.assertFalse(any(getattr(task.get_coro(), '__name__', '') == 'delivery_loop' for task in asyncio.all_tasks()))


if __name__ == "__main__":
    unittest.main()

"""Account-wide offer/feedback regressions on a dedicated local MySQL database."""
import asyncio
from datetime import timedelta
import os
import secrets
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url

from app import payment_routes, subscription_offers as offers
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo, db_session
from app.db.engine import db_engine, init_db
from app.db.models import Organization, OrgMember, Payment, PromoCode, Session, SubscriptionOfferImpression as Impression, SubscriptionOfferState as OfferState, User
from app.db.repo import utcnow
from app.main import app

URL = os.environ.get("TEST_DATABASE_URL", "")
SAFE = bool(URL and make_url(URL).host in {"localhost", "127.0.0.1"} and (make_url(URL).database or "").endswith("_test"))


@unittest.skipUnless(SAFE, "TEST_DATABASE_URL must point to a dedicated local *_test database")
class SubscriptionOfferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(settings, "DATABASE_URL", URL), patch.object(settings, "FREE_PERIOD", False),
            patch.object(settings, "FREE_TRIAL_DAYS", 1), patch.object(settings, "AUTH_JWT_SECRET", secrets.token_hex(32)),
            patch.object(settings, "ADMIN_TOKEN", secrets.token_hex(32)), patch.object(settings, "YOOKASSA_SHOP_ID", "synthetic"),
            patch.object(settings, "YOOKASSA_SECRET_KEY", "synthetic"), patch.object(settings, "YOOKASSA_RECURRING_ENABLED", False)]
        for item in self.patches:
            item.start()
        init_db()
        self.users = []; self.orgs = []
        async with db_session() as session:
            repo = Repo(session)
            for _ in range(2):
                user = await repo.create_native_user(email=f"offer-{uuid4().hex}@example.invalid", password_hash="unused", first_name="Offer test")
                user.created_at = utcnow() - timedelta(days=3)
                user.tutorial_done_at = utcnow()
                self.users.append(user.id)
            for code, percent in (("SALE20", 20), ("OLD50", 50)):
                await repo.ensure_promo(code, percent)
                await session.execute(update(PromoCode).where(PromoCode.code == code).values(active=True, discount_percent=percent))
        self.uid, self.other = self.users
        self.headers = {"Authorization": f"Bearer {issue_jwt(self.uid)}"}
        self.other_headers = {"Authorization": f"Bearer {issue_jwt(self.other)}"}
        self.admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test", trust_env=False)

    async def asyncTearDown(self):
        await self.client.aclose()
        async with db_session() as session:
            await session.execute(delete(User).where(User.id.in_(self.users)))
            if self.orgs:
                await session.execute(delete(Organization).where(Organization.id.in_(self.orgs)))
            for code, percent in (("SALE20", 20), ("OLD50", 50)):
                await session.execute(update(PromoCode).where(PromoCode.code == code).values(active=True, discount_percent=percent))
        await db_engine().dispose()
        for item in reversed(self.patches):
            item.stop()

    async def post(self, path, body=None, *, headers=None, status=200):
        response = await self.client.post("/api/subscription-offers" + path, json=body or {}, headers=headers or self.headers)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    async def claim(self, request_id=None, **kwargs):
        return await self.post("/claim", {"request_id": request_id or str(uuid4())}, **kwargs)

    async def answer(self, offer, reason="price", **kwargs):
        return await self.post("/answer", {"impression_id": offer["impression_id"], "reason": reason}, **kwargs)

    async def account(self, **values):
        async with db_session() as session:
            await session.execute(update(User).where(User.id == self.uid).values(**values))

    async def payment(self, *, uid=None, code=None, status="succeeded", amount=500, plan="monthly", created=None):
        async with db_session() as session:
            row = Payment(user_id=uid or self.uid, amount_rub=amount, plan=plan, status=status, days_granted=30,
                promo_code=code, created_at=created or utcnow(), updated_at=utcnow())
            session.add(row); await session.flush()
            return row.id

    async def test_expired_trial_first_return_and_account_wide_cooldown(self):
        first = await self.claim()
        self.assertTrue(first["show"])
        self.assertEqual((first["promo_code"], first["discount_percent"]), ("SALE20", 20))
        self.assertEqual(first["audience"], "trial_expired")
        self.assertEqual(len(first["reasons"]), 8)
        self.assertEqual(first["reasons"][-1], {"code": "other", "label": "Другое"})
        self.assertFalse((await self.claim())["show"])
        self.assertTrue((await self.claim(headers=self.other_headers))["show"])

    async def test_request_retry_recovers_same_impression_without_another_show(self):
        key = str(uuid4())
        self.assertEqual(await self.claim(key), await self.claim(key))
        async with db_session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(Impression).where(Impression.user_id == self.uid)), 1)

    async def test_simultaneous_devices_receive_only_one_offer(self):
        results = await asyncio.gather(*(self.claim() for _ in range(6)))
        self.assertEqual(sum(result["show"] for result in results), 1)

    async def test_close_is_not_an_answer_and_exact_24_hour_boundary(self):
        first = await self.claim()
        await self.post("/event", {"impression_id": first["impression_id"], "action": "closed"})
        self.assertFalse((await self.claim(first["impression_id"]))["show"])
        now = utcnow()
        async with db_session() as session:
            await session.execute(update(OfferState).where(OfferState.user_id == self.uid).values(last_shown_at=now - timedelta(hours=24) + timedelta(seconds=1)))
        with patch.object(offers, "utcnow", return_value=now):
            self.assertFalse((await self.claim())["show"])
        async with db_session() as session:
            await session.execute(update(OfferState).where(OfferState.user_id == self.uid).values(last_shown_at=now - timedelta(hours=24)))
        with patch.object(offers, "utcnow", return_value=now):
            self.assertTrue((await self.claim())["show"])

    async def test_answer_is_saved_once_and_permanently_suppresses_popup(self):
        first = await self.claim()
        result = await self.answer(first, "other")
        self.assertEqual((result["promo_code"], result["discount_percent"]), ("OLD50", 50))
        repeated = await self.answer(first, "time")
        self.assertEqual(repeated["reason"], "other")
        with patch.object(offers, "utcnow", return_value=utcnow() + timedelta(days=500)):
            self.assertFalse((await self.claim())["show"])
            self.assertFalse((await self.claim(first["impression_id"]))["show"])

    async def test_simultaneous_answers_keep_the_first_saved_reason(self):
        first = await self.claim()
        results = await asyncio.gather(self.answer(first, "price"), self.answer(first, "time"))
        self.assertEqual(results[0]["reason"], results[1]["reason"])

    async def test_fixed_choices_only_and_no_free_text(self):
        first = await self.claim()
        await self.answer(first, "custom text", status=422)
        await self.post("/answer", {"impression_id": first["impression_id"], "reason": "other", "text": "arbitrary"}, status=422)
        async with db_session() as session:
            self.assertIsNone((await session.get(OfferState, self.uid)).answered_at)

    async def test_another_account_cannot_answer_or_record_events(self):
        first = await self.claim()
        await self.answer(first, headers=self.other_headers, status=404)
        await self.post("/event", {"impression_id": first["impression_id"], "action": "tariffs"}, headers=self.other_headers, status=404)
        await self.claim(first["impression_id"], headers=self.other_headers, status=409)

    async def test_events_are_idempotent_and_old50_click_requires_saved_answer(self):
        first = await self.claim()
        body = {"impression_id": first["impression_id"], "action": "tariffs", "promo_code": "OLD50"}
        await self.post("/event", body, status=409)
        await self.post("/event", {**body, "promo_code": "SALE20"})
        await self.post("/event", {"impression_id": first["impression_id"], "action": "declined"})
        await self.answer(first)
        await self.post("/event", body)
        async with db_session() as session:
            stamp = (await session.get(Impression, first["impression_id"])).followup_clicked_at
        await self.post("/event", body)
        await self.post("/event", {"impression_id": first["impression_id"], "action": "closed"})
        async with db_session() as session:
            row = await session.get(Impression, first["impression_id"])
            self.assertEqual(row.followup_clicked_at, stamp)
            self.assertIsNone(row.closed_at)

    async def test_active_trial_is_excluded(self):
        await self.account(created_at=utcnow())
        self.assertFalse((await self.claim())["show"])

    async def test_paid_subscription_is_excluded(self):
        await self.account(subscription_until=utcnow() + timedelta(days=20))
        self.assertFalse((await self.claim())["show"])

    async def test_former_paying_user_is_excluded(self):
        await self.payment()
        self.assertFalse((await self.claim())["show"])

    async def test_free_period_and_blocked_accounts_are_excluded(self):
        with patch.object(settings, "FREE_PERIOD", True):
            self.assertFalse((await self.claim())["show"])
        await self.account(is_blocked=True)
        self.assertFalse((await self.claim())["show"])

    async def test_active_school_access_is_excluded(self):
        async with db_session() as session:
            org = Organization(name="Offer school", invite_code=uuid4().hex, valid_until=utcnow() + timedelta(days=30), created_at=utcnow())
            session.add(org); await session.flush(); self.orgs.append(org.id)
            session.add(OrgMember(org_id=org.id, user_id=self.uid, role="student", active=True, joined_at=utcnow()))
        self.assertFalse((await self.claim())["show"])

    async def test_existing_free_learner_without_trial_is_included(self):
        with patch.object(settings, "FREE_TRIAL_DAYS", 0):
            self.assertFalse((await self.claim())["show"])
            async with db_session() as session:
                session.add(Session(user_id=self.uid, started_at=utcnow(), used_seconds=30, mode="chat"))
            self.assertEqual((await self.claim())["audience"], "free_learner")

    async def test_unavailable_or_wrong_discount_is_never_advertised(self):
        for code in ("SALE20", "OLD50"):
            async with db_session() as session:
                await session.execute(update(PromoCode).where(PromoCode.code == code).values(active=False))
            self.assertFalse((await self.claim())["show"])
            async with db_session() as session:
                await session.execute(update(PromoCode).where(PromoCode.code == code).values(active=True, discount_percent=10))
            self.assertFalse((await self.claim())["show"])
            async with db_session() as session:
                await session.execute(update(PromoCode).where(PromoCode.code == code).values(discount_percent=20 if code == "SALE20" else 50))

    async def test_an_already_used_discount_is_not_offered_again(self):
        async with db_session() as session:
            await Repo(session).record_promo_activation(code="OLD50",user_id=self.uid,payment_id=None,discount_percent=50)
        self.assertFalse((await self.claim())["show"])

    async def test_user_and_admin_endpoints_require_authentication(self):
        response = await self.client.post("/api/subscription-offers/claim", json={"request_id": str(uuid4())})
        self.assertEqual(response.status_code, 401)
        response = await self.client.get("/api/admin/subscription-offers")
        self.assertEqual(response.status_code, 401)

    async def test_admin_counts_reasons_and_attributes_each_promo_only_after_view(self):
        # An earlier SALE20 purchase must not be attributed to this campaign.
        old = await self.payment(uid=self.other, code="SALE20", created=utcnow() - timedelta(days=10), status="pending")
        first = await self.claim()
        second = await self.claim(headers=self.other_headers)
        await self.answer(first, "price")
        await self.answer(second, "time", headers=self.other_headers)
        for code in ("SALE20", "OLD50"):
            await self.post("/event", {"impression_id": first["impression_id"], "action": "tariffs", "promo_code": code})
        await self.payment(code="OLD50", amount=500)
        await self.payment(uid=self.other, code="SALE20", amount=800)
        async with db_session() as session:
            await session.execute(update(Payment).where(Payment.id == old).values(status="succeeded"))
        response = await self.client.get("/api/admin/subscription-offers?days=7", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data["impressions"], data["users_shown"], data["answers"], data["paid_users"]), (2, 2, 2, 2))
        self.assertEqual(data["revenue_rub"], 1300)
        self.assertEqual((data["sale20_tariff_clicks"], data["old50_tariff_clicks"]), (1, 1))
        self.assertEqual([item["count"] for item in data["reasons"][:2]], [1, 1])
        self.assertEqual([item["users"] for item in data["payments_by_promo"]], [1, 1])
        self.assertEqual(sum(point["shown"] for point in data["daily"]), 2)
        self.assertEqual(sum(point["answers"] for point in data["daily"]), 2)
        self.assertEqual(len(data["daily"]), 7)

    async def test_both_discounts_work_on_every_existing_plan(self):
        for code, percent in (("SALE20", 20), ("OLD50", 50)):
            for key, info in payment_routes._plan_catalog().items():
                with self.subTest(code=code, plan=key):
                    mock = AsyncMock(return_value={"id": str(uuid4()), "confirmation": {"confirmation_url": "https://example.invalid/checkout"}})
                    with patch.object(payment_routes.yk, "create_payment", mock):
                        response = await self.client.post("/api/payments/create", headers=self.headers, json={"plan": key, "promo_code": code})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(mock.call_args.kwargs["amount_rub"], int(info["amount_rub"] * (100-percent) / 100 + .5))

    async def test_fast_checkout_is_attributed_despite_coarse_payment_timestamp(self):
        first = await self.claim()
        await self.answer(first)
        async with db_session() as session:
            shown = (await session.get(Impression, first["impression_id"])).shown_at
        await self.payment(code="OLD50",created=shown.replace(microsecond=0))
        response=await self.client.get("/api/admin/subscription-offers",headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["paid_users"],1)

    async def test_half_ruble_rounding_matches_the_displayed_price(self):
        with patch.object(settings, "SUBSCRIPTION_PRICE_MONTHLY_RUB", 1001), patch.object(payment_routes.yk, "create_payment", AsyncMock(
            return_value={"id": str(uuid4()), "confirmation": {"confirmation_url": "https://example.invalid/checkout"}})) as mock:
            response = await self.client.post("/api/payments/create", headers=self.headers, json={"plan": "monthly", "promo_code": "OLD50"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock.call_args.kwargs["amount_rub"], 501)


if __name__ == "__main__":
    unittest.main()

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

from app import recurring, payment_routes, subscription_offers as offers
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo, db_session
from app.db.engine import db_engine, init_db
from app.db.models import Organization, OrgMember, Payment, PromoCode, Session, SubscriptionOfferImpression as Impression, SubscriptionOfferState as OfferState, User
from app.db.repo import utcnow
from app.main import app
from app.learning_models import LearningProfile
from app.db.models import RecurringSubscription

URL = os.environ.get("TEST_DATABASE_URL", "")
SAFE = bool(URL and make_url(URL).host in {"localhost", "127.0.0.1"} and (make_url(URL).database or "").endswith("_test"))


@unittest.skipUnless(SAFE, "TEST_DATABASE_URL must point to a dedicated local *_test database")
class SubscriptionOfferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(settings, "DATABASE_URL", URL), patch.object(settings, "FREE_PERIOD", False),
            patch.object(settings, "FREE_TRIAL_DAYS", 1), patch.object(settings, "AUTH_JWT_SECRET", secrets.token_hex(32)),
            patch.object(settings, "ADMIN_TOKEN", secrets.token_hex(32)), patch.object(settings, "YOOKASSA_SHOP_ID", "synthetic"),
            patch.object(settings, "YOOKASSA_SECRET_KEY", "synthetic"), patch.object(settings, "YOOKASSA_RECURRING_ENABLED", True)]
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
                session.add(LearningProfile(user_id=user.id,goal="general",level="A1",completed_count=1,checkpoint_count=0,created_at=utcnow(),updated_at=utcnow()))
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
            await session.execute(delete(RecurringSubscription).where(RecurringSubscription.user_id.in_(self.users)))
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

    async def claim(self, request_id=None, trigger="entry", **kwargs):
        return await self.post("/claim", {"request_id": request_id or str(uuid4()), "trigger": trigger}, **kwargs)

    async def answer(self, offer, reason="price", **kwargs):
        return await self.post("/answer", {"impression_id": offer["impression_id"], "reason": reason}, **kwargs)

    async def account(self, **values):
        async with db_session() as session:
            await session.execute(update(User).where(User.id == self.uid).values(**values))

    async def payment(self, *, uid=None, code=None, status="succeeded", amount=500, plan="monthly", created=None):
        async with db_session() as session:
            row = Payment(user_id=uid or self.uid, amount_rub=amount, plan=plan, status=status, days_granted=30,
                promo_code=code, checkout_data={"consented_at":"test"} if plan=="trial7" else None, created_at=created or utcnow(), updated_at=utcnow())
            session.add(row); await session.flush()
            return row.id

    async def test_expired_trial_first_return_and_account_wide_cooldown(self):
        first = await self.claim()
        self.assertTrue(first["show"])
        self.assertEqual((first["plan"], first["amount_rub"], first["days"]), ("trial7", 1, 7))
        self.assertEqual(first["audience"], "access_required")
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
        self.assertTrue(result["ok"]); self.assertNotIn("promo_code", result)
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
        await self.post("/event", {"impression_id": first["impression_id"], "action": "checkout"}, headers=self.other_headers, status=404)
        await self.claim(first["impression_id"], headers=self.other_headers, status=409)

    async def test_events_are_idempotent_and_answer_has_no_followup_offer(self):
        first = await self.claim()
        body = {"impression_id": first["impression_id"], "action": "checkout"}
        await self.post("/event", body)
        async with db_session() as session:
            stamp = (await session.get(Impression, first["impression_id"])).clicked_at
        await self.post("/event", body)
        await self.answer(first)
        async with db_session() as session:
            self.assertEqual((await session.get(Impression, first["impression_id"])).clicked_at, stamp)
        manual = await self.claim(trigger="access")
        self.assertTrue(manual["show"]); self.assertTrue(manual["answered"])
        self.assertEqual(manual["amount_rub"], 1)

    async def test_stale_welcome_days_do_not_restore_full_access(self):
        await self.account(created_at=utcnow())
        self.assertTrue((await self.claim())["show"])

    async def test_paid_subscription_is_excluded(self):
        await self.account(subscription_until=utcnow() + timedelta(days=20))
        self.assertFalse((await self.claim())["show"])

    async def test_former_paying_user_is_excluded(self):
        await self.payment()
        self.assertFalse((await self.claim())["show"])

    async def test_legacy_free_period_does_not_unlock_practice_and_blocked_accounts_are_excluded(self):
        with patch.object(settings, "FREE_PERIOD", True):
            self.assertTrue((await self.claim())["show"])
        await self.account(is_blocked=True)
        self.assertFalse((await self.claim())["show"])

    async def test_admin_filter_and_renewal_are_independent(self):
        marker = uuid4().hex
        async with db_session() as session:
            await session.execute(update(User).where(User.id.in_(self.users)).values(first_name=marker))
            (await session.get(User, self.uid)).subscription_until = utcnow() + timedelta(days=7)
            session.add(RecurringSubscription(user_id=self.uid, plan="monthly", status="active",
                payment_method_id="test", amount_rub=999, next_charge_at=utcnow()+timedelta(days=7),
                created_at=utcnow(), updated_at=utcnow()))
        async def filtered(active):
            response = await self.client.get("/api/admin/users", params={"q": marker, "has_subscription": active},
                                             headers=self.admin)
            self.assertEqual(response.status_code, 200, response.text)
            return [(row["id"], row["auto_renew_enabled"]) for row in response.json()]
        self.assertEqual(await filtered(True), [(self.uid, True)])
        self.assertEqual(await filtered(False), [(self.other, False)])
        async with db_session() as session:
            await session.execute(update(RecurringSubscription).where(RecurringSubscription.user_id == self.uid)
                                  .values(status="canceled", payment_method_id=""))
        self.assertEqual(await filtered(True), [(self.uid, False)])
        detail = await self.client.get(f"/api/admin/users/{self.uid}", headers=self.admin)
        self.assertTrue(detail.json()["has_subscription"])
        self.assertFalse(detail.json()["auto_renew_enabled"])

    async def test_ltv_counts_all_personal_payments_once_per_payer(self):
        async with db_session() as session:
            payments = (await session.scalars(select(Payment).where(Payment.status == "succeeded"))).all()
            personal = [p for p in payments if p.amount_rub > 0 and p.plan in
                        {"trial3","trial7","monthly","yearly","twoyear","manual_pay"}]
            base = sum(p.amount_rub for p in personal)
            payers = len({p.user_id for p in personal})
            if payers == 0:
                self.assertIsNone(await Repo(session).paid_subscriber_ltv())
        await self.payment(plan="trial7", amount=1)
        await self.payment(amount=999)
        await self.payment(uid=self.other, plan="manual_pay", amount=499)
        for plan in ("gift", "org", "admin_grant", "referral"):
            await self.payment(plan=plan, amount=100)
        for status in ("pending", "canceled", "refunded"):
            await self.payment(status=status, amount=999)
        from app.admin import _build_metrics
        metrics = await _build_metrics()
        self.assertEqual(metrics.paid_subscriber_ltv_rub, float(round((base+1499)/(payers+2), 2)))

    async def test_active_school_access_is_excluded(self):
        async with db_session() as session:
            org = Organization(name="Offer school", invite_code=uuid4().hex, valid_until=utcnow() + timedelta(days=30), created_at=utcnow())
            session.add(org); await session.flush(); self.orgs.append(org.id)
            session.add(OrgMember(org_id=org.id, user_id=self.uid, role="student", active=True, joined_at=utcnow()))
        self.assertFalse((await self.claim())["show"])
        async with db_session() as session:
            rows = await Repo(session).search_users("Offer test", has_subscription=True)
            self.assertIn(self.uid, [user.id for user, active, renewal in rows if active and not renewal])

    async def test_new_user_receives_offer_before_first_lesson(self):
        async with db_session() as session:
            await session.execute(update(LearningProfile).where(LearningProfile.user_id==self.uid).values(completed_count=0))
        self.assertTrue((await self.claim())["show"])
        self.assertFalse((await self.claim(trigger="lesson_completed"))["show"])
        self.assertTrue((await self.claim(trigger="access"))["show"])

    async def test_onboarding_defers_entry_offer_but_practice_always_offers_checkout(self):
        await self.account(tutorial_done_at=None)
        self.assertFalse((await self.claim())["show"])
        self.assertTrue((await self.claim(trigger="access"))["show"])

    async def test_legacy_completion_does_not_revive_dismissed_offer(self):
        first = await self.claim(trigger="access")
        await self.post("/event", {"impression_id":first['impression_id'], "action":"closed"})
        self.assertFalse((await self.claim(trigger="lesson_completed"))["show"])

    async def test_disabled_recurring_does_not_sell_a_nonrenewing_ruble_week(self):
        with patch.object(settings,"YOOKASSA_RECURRING_ENABLED",False):
            self.assertFalse((await self.claim())["available"])
            response=await self.checkout()
            self.assertEqual(response.status_code,503)

    async def test_user_and_admin_endpoints_require_authentication(self):
        response = await self.client.post("/api/subscription-offers/claim", json={"request_id": str(uuid4())})
        self.assertEqual(response.status_code, 401)
        response = await self.client.get("/api/admin/subscription-offers")
        self.assertEqual(response.status_code, 401)

    async def test_admin_counts_new_campaign_and_excludes_old_discounts_and_renewals(self):
        old = await self.payment(uid=self.other, code="SALE20", status="pending")
        first = await self.claim()
        second = await self.claim(headers=self.other_headers)
        await self.answer(first, "price")
        await self.answer(second, "time", headers=self.other_headers)
        await self.post("/event", {"impression_id":first["impression_id"],"action":"checkout"})
        await self.payment(plan="trial7",amount=1)
        await self.payment(plan="monthly",amount=999)
        async with db_session() as session:
            await session.execute(update(Payment).where(Payment.id==old).values(status="succeeded"))
        response=await self.client.get("/api/admin/subscription-offers?days=7",headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual((data["impressions"],data["users_shown"],data["answers"],data["paid_users"]),(2,2,2,1))
        self.assertEqual(data["revenue_rub"],1)
        self.assertEqual([r["count"] for r in data["reasons"][:2]],[1,1])
        self.assertEqual(sum(r["shown"] for r in data["daily"]),2)

    async def test_retired_discounts_are_rejected_even_when_admin_left_them_active(self):
        for code in ("SALE20","OLD50"):
            check=await self.client.get("/api/payments/promo/check",params={"code":code},headers=self.headers)
            self.assertFalse(check.json()["valid"])
            response=await self.client.post("/api/payments/create",headers=self.headers,json={"plan":"monthly","promo_code":code})
            self.assertEqual(response.status_code,400,response.text)

    async def checkout(self, **extra):
        return await self.client.post("/api/payments/create",headers=self.headers,json={
            "plan":"trial7","recurring_consent":True,"renew_amount_rub":settings.SUBSCRIPTION_PRICE_MONTHLY_RUB,**extra})

    def provider(self):
        return {"id":str(uuid4()),"confirmation":{"confirmation_url":"https://example.invalid/checkout"}}

    async def test_checkout_requires_consent_and_current_renewal_price(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock()) as mock:
            for extra in ({"recurring_consent":False},{"renew_amount_rub":1},{"promo_code":"SALE20"}):
                response=await self.checkout(**extra)
                self.assertIn(response.status_code,(400,409),response.text)
            mock.assert_not_awaited()

    async def test_profile_catalog_has_four_plans_and_one_ruble_intro(self):
        with patch.object(settings, 'SUBSCRIPTION_PRICE_TRIAL7_RUB', 199):
            response = await self.client.get('/api/payments/plans', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        plans = {plan['key']: plan for plan in response.json()['plans']}
        self.assertEqual(set(plans), {'trial7', 'monthly', 'yearly', 'twoyear'})
        self.assertEqual((plans['trial7']['amount_rub'], plans['trial7']['days']), (1, 7))
        self.assertTrue(plans['trial7']['recurring'])
        self.assertEqual(plans['trial7']['renew_amount_rub'], plans['monthly']['amount_rub'])
        self.assertEqual(plans['trial7']['renew_days'], 30)

    async def test_ruble_checkout_snapshots_price_and_reuses_payment_across_devices(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value=self.provider())) as mock:
            results=await asyncio.gather(*(self.checkout() for _ in range(5)))
            self.assertTrue(all(r.status_code==200 for r in results),[r.text for r in results])
            self.assertEqual(len({r.json()["payment_id"] for r in results}),1)
            self.assertEqual(len({c.kwargs["idempotence_key"] for c in mock.call_args_list}),1)
            self.assertTrue(all(c.kwargs["require_recurring"] and c.kwargs["amount_rub"]==1 for c in mock.call_args_list))
            again=await self.checkout()
            self.assertEqual(again.json(),results[0].json())
        async with db_session() as session:
            row=await session.get(Payment,again.json()["payment_id"])
            self.assertEqual((row.amount_rub,row.days_granted),(1,7))
            self.assertEqual(row.checkout_data["renew_amount_rub"],settings.SUBSCRIPTION_PRICE_MONTHLY_RUB)

    async def test_provider_timeout_retries_same_key_and_payload(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(side_effect=[None,self.provider()])) as mock:
            self.assertEqual((await self.checkout()).status_code,502)
            self.assertEqual((await self.checkout()).status_code,200)
            self.assertEqual(mock.call_args_list[0],mock.call_args_list[1])

    async def test_used_or_refunded_intro_cannot_be_purchased_again(self):
        for status in ("succeeded","refunded"):
            async with db_session() as session:
                await session.execute(delete(Payment).where(Payment.user_id==self.uid))
            await self.payment(plan="trial7",amount=1,status=status)
            self.assertEqual((await self.checkout()).status_code,400)
            offer=await self.claim(trigger="access")
            self.assertEqual(offer["plan"],"monthly")
            self.assertEqual(offer["amount_rub"],settings.SUBSCRIPTION_PRICE_MONTHLY_RUB)
            catalog = await self.client.get('/api/payments/plans', headers=self.headers)
            self.assertEqual({plan['key'] for plan in catalog.json()['plans']}, {'monthly', 'yearly', 'twoyear'})

    async def test_credit_full_week_renewal_amount_and_cancel_survive_duplicate_webhook(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value=self.provider())):
            response=await self.checkout()
        self.assertEqual(response.status_code,200,response.text)
        pid=response.json()["payment_id"]
        confirmed={"metadata":{"recurring_setup":"1"},"payment_method":{"id":"saved-test","saved":True,"type":"bank_card","card":{"last4":"4242"}}}
        async with db_session() as session:
            repo=Repo(session)
            await repo.credit_subscription_for_payment(pid)
            payment=await session.get(Payment,pid)
            with patch.object(settings,"SUBSCRIPTION_PRICE_MONTHLY_RUB",1999):
                await recurring.after_credit(repo,payment,confirmed)
            sub=await session.scalar(select(RecurringSubscription).where(RecurringSubscription.user_id==self.uid))
            user=await session.get(User,self.uid)
            self.assertEqual(sub.amount_rub,999)
            self.assertEqual(sub.next_charge_at,user.subscription_until)
            self.assertEqual(sub.charge_before_hours,0)
            self.assertGreater(user.subscription_until,utcnow()+timedelta(days=6,hours=23))
            sub.status="canceled";sub.payment_method_id=""
            await session.flush()
            await recurring.after_credit(repo,payment,confirmed)
            self.assertEqual(sub.status,"canceled")
            self.assertEqual(sub.payment_method_id,"")

    async def test_open_checkout_keeps_previously_agreed_renewal_price(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value=self.provider())):
            response=await self.checkout()
        self.assertEqual(response.status_code,200,response.text)
        with patch.object(settings,"SUBSCRIPTION_PRICE_MONTHLY_RUB",1999):
            offer=await self.claim(trigger="access")
            self.assertEqual(offer["renew_amount_rub"],999)
            catalog = await self.client.get('/api/payments/plans', headers=self.headers)
            plans = {plan['key']: plan for plan in catalog.json()['plans']}
            self.assertEqual(plans['trial7']['renew_amount_rub'], 999)
            self.assertEqual(plans['monthly']['amount_rub'], 1999)
            again=await self.checkout(renew_amount_rub=999)
            self.assertEqual(again.json(),response.json())

    async def test_definite_provider_rejection_releases_checkout_without_fallback(self):
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value={"_creation_rejected":True})):
            self.assertEqual((await self.checkout()).status_code,503)
        async with db_session() as session:
            row=await session.scalar(select(Payment).where(Payment.user_id==self.uid))
            self.assertEqual(row.status,"canceled")
        with patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value=self.provider())):
            self.assertEqual((await self.checkout()).status_code,200)

    async def test_strict_ruble_provider_request_never_falls_back_to_unsaved_card(self):
        client=AsyncMock()
        client.post.return_value=httpx.Response(400,json={"code":"invalid_request"})
        client.__aenter__.return_value=client
        with patch('app.yookassa.httpx.AsyncClient',return_value=client):
            result=await payment_routes.yk.create_payment(amount_rub=1,description='test',return_url='https://example.invalid',
                metadata={},customer_email='test@example.invalid',save_payment_method=True,require_recurring=True,idempotence_key='fixed')
        self.assertTrue(result['_creation_rejected'])
        self.assertEqual(client.post.await_count,1)
        self.assertEqual(client.post.call_args.kwargs['json']['payment_method_data'],{'type':'bank_card'})
        self.assertTrue(client.post.call_args.kwargs['json']['save_payment_method'])

    async def test_fast_checkout_attribution_does_not_lose_subsecond_impression(self):
        first=await self.claim()
        async with db_session() as session:
            shown=(await session.get(Impression,first['impression_id'])).shown_at
        await self.payment(plan="trial7",amount=1,created=shown.replace(microsecond=0))
        data=(await self.client.get('/api/admin/subscription-offers',headers=self.admin)).json()
        self.assertEqual(data['paid_users'],1)

    async def test_custom_promo_rounding_remains_compatible(self):
        async with db_session() as session:
            await Repo(session).ensure_promo("ROUNDING_TEST",50)
        with patch.object(settings,"SUBSCRIPTION_PRICE_MONTHLY_RUB",1001),patch.object(payment_routes.yk,"create_payment",AsyncMock(return_value=self.provider())) as mock:
            response=await self.client.post('/api/payments/create',headers=self.headers,json={'plan':'monthly','promo_code':'ROUNDING_TEST'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(mock.call_args.kwargs['amount_rub'],501)


if __name__ == "__main__":
    unittest.main()

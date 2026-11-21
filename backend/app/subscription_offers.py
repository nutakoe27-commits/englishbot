"""Subscription offer, fixed-choice feedback, and administrator campaign analytics."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .admin import require_admin_token
from .auth import resolve_user
from .config import settings
from .db import Repo, db_session
from .db.models import Payment, SubscriptionOfferImpression as Impression, SubscriptionOfferState as OfferState, User
from .db.repo import utcnow

CAMPAIGN = "intro_week"
REASONS = {
    "price": "Для меня дорого",
    "time": "Не хватает времени заниматься",
    "uncertain": "Пока не понял(а), подходит ли мне сервис",
    "free_enough": "Мне хватает бесплатных возможностей",
    "missing_features": "Не хватает нужных функций или материалов",
    "technical": "Возникли технические проблемы",
    "payment": "Не получается оплатить",
    "other": "Другое",
}
Reason = Literal["price", "time", "uncertain", "free_enough", "missing_features", "technical", "payment", "other"]


def require_db():
    if not settings.DATABASE_URL:
        raise HTTPException(503, "db_not_configured")


router = APIRouter(prefix="/api/subscription-offers", dependencies=[Depends(require_db)], tags=["Subscription offers"])
admin_router = APIRouter(prefix="/api/admin/subscription-offers", dependencies=[Depends(require_admin_token)], tags=["admin"])


class OfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    init_data: Optional[str] = None


class ClaimRequest(OfferRequest):
    request_id: UUID
    trigger: Literal["entry", "access", "lesson_completed"] = "entry"


class EventRequest(OfferRequest):
    impression_id: UUID
    action: Literal["declined", "closed", "checkout"]


class AnswerRequest(OfferRequest):
    impression_id: UUID
    reason: Reason


async def _locked_user(session, authorization, init_data):
    if not settings.DATABASE_URL:
        raise HTTPException(503, "db_not_configured")
    user = await resolve_user(Repo(session), authorization=authorization, init_data=init_data or "")
    # Lock an existing parent row even when this account has no offer state yet.
    # This serializes claims and answers across tabs, devices and API workers.
    return await session.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))


async def _audience(session, user, trigger):
    from .payment_routes import intro_eligible
    if user.is_blocked or await Repo(session).has_full_access(user):
        return None
    if trigger == "access":
        return "access_required"
    if trigger == "entry" and user.tutorial_done_at and await intro_eligible(Repo(session), user):
        return "access_required"
    return None


async def _offer(session, user, impression, state):
    from .payment_routes import intro_eligible, intro_renewal_amount, _plan_catalog
    from . import recurring
    eligible = await intro_eligible(Repo(session), user)
    plan = 'trial7' if eligible else 'monthly'
    info = _plan_catalog()[plan]
    renewal = _plan_catalog()['monthly']['amount_rub']
    if eligible:
        renewal = await intro_renewal_amount(Repo(session), user.id)
    return {"show": True, "impression_id": impression.id, "audience": impression.audience,
            "answered": bool(state and state.answered_at), "plan": plan,
            "amount_rub": info['amount_rub'], "days": info['days'],
            "renew_amount_rub": renewal,
            "available": recurring.enabled(),
            "reasons": [{"code": code, "label": label} for code, label in REASONS.items()]}


async def _state(session, user_id):
    # Under MySQL REPEATABLE READ, authentication may already have opened a
    # snapshot before waiting for the User lock. A locking read is needed here
    # to observe a preceding device's committed claim/answer, not that snapshot.
    return await session.scalar(select(OfferState).where(OfferState.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True))


@router.post("/claim")
async def claim(body: ClaimRequest, authorization: Optional[str] = Header(None)):
    async with db_session() as session:
        user = await _locked_user(session, authorization, body.init_data)
        now = utcnow()
        state = await _state(session, user.id)
        if state and state.answered_at and body.trigger != "access":
            return {"show": False, "answered": True}
        audience = await _audience(session, user, body.trigger)
        if not audience:
            return {"show": False}
        previous = await session.scalar(select(Impression).where(Impression.id == str(body.request_id)).with_for_update())
        if previous:
            if previous.user_id != user.id:
                raise HTTPException(409, "request_conflict")
            # A transport retry may recover the same unconsumed response, but
            # cannot manufacture a second impression or revive a closed popup.
            if previous.shown_at >= now - timedelta(minutes=5) and not previous.closed_at and not previous.clicked_at:
                return await _offer(session, user, previous, state)
            return {"show": False}
        if body.trigger == "entry" and state and state.last_shown_at > now - timedelta(hours=24):
            return {"show": False}
        if state is None:
            state = OfferState(user_id=user.id, first_shown_at=now, last_shown_at=now)
            session.add(state)
        else:
            state.last_shown_at = now
        # Existing payments use second-resolution timestamps. Capture the latest
        # payment ID with a current read so subsecond rounding cannot attribute
        # an older checkout or discard a fast checkout after this impression.
        cursor = await session.scalar(select(Payment.id).where(Payment.user_id == user.id)
            .order_by(Payment.id.desc()).limit(1).with_for_update())
        impression = Impression(campaign=CAMPAIGN, id=str(body.request_id), user_id=user.id, audience=audience, shown_at=now, payment_cursor=cursor or 0)
        session.add(impression)
        await session.flush()
        return await _offer(session, user, impression, state)


async def _owned_impression(session, user, impression_id):
    impression = await session.scalar(select(Impression).where(Impression.id == str(impression_id)).with_for_update())
    if not impression or impression.user_id != user.id:
        raise HTTPException(404, "offer_not_found")
    return impression


@router.post("/event")
async def event(body: EventRequest, authorization: Optional[str] = Header(None)):
    async with db_session() as session:
        user = await _locked_user(session, authorization, body.init_data)
        impression = await _owned_impression(session, user, body.impression_id)
        state = await _state(session, user.id)
        if body.action == "checkout":
            impression.initial_clicked_at = impression.initial_clicked_at or utcnow()
            impression.clicked_at = impression.clicked_at or utcnow()
        elif not state or not state.answered_at:
            field = "declined_at" if body.action == "declined" else "closed_at"
            setattr(impression, field, getattr(impression, field) or utcnow())
        return {"ok": True}


@router.post("/answer")
async def answer(body: AnswerRequest, authorization: Optional[str] = Header(None)):
    async with db_session() as session:
        user = await _locked_user(session, authorization, body.init_data)
        impression = await _owned_impression(session, user, body.impression_id)
        state = await _state(session, user.id)
        if not state:
            raise HTTPException(409, "offer_state_missing")
        # Repeated taps/retries preserve the first saved answer.
        if not state.answered_at:
            state.reason_code = body.reason
            state.answered_at = utcnow()
            state.answer_impression_id = impression.id
        return {"ok": True, "reason": state.reason_code}


@admin_router.get("")
async def analytics(days: int = Query(30, ge=1, le=365)):
    if not settings.DATABASE_URL:
        raise HTTPException(503, "db_not_configured")
    today = (utcnow() + timedelta(hours=3)).date()
    since = datetime.combine(today - timedelta(days=days-1), time.min) - timedelta(hours=3)
    async with db_session() as session:
        count, users, declines, closes, clicks, click_users, initial_clicks, followup_clicks = (await session.execute(select(
            func.count(), func.count(func.distinct(Impression.user_id)), func.count(Impression.declined_at),
            func.count(Impression.closed_at), func.count(Impression.clicked_at),
            func.count(func.distinct(func.if_(Impression.clicked_at.is_not(None), Impression.user_id, None))),
            func.count(Impression.initial_clicked_at),
            func.count(Impression.followup_clicked_at),
        ).where(Impression.campaign == CAMPAIGN, Impression.shown_at >= since))).one()
        answers = list((await session.execute(select(OfferState.reason_code, func.count())
            .join(Impression, Impression.id == OfferState.answer_impression_id)
            .where(Impression.campaign == CAMPAIGN, Impression.shown_at >= since).group_by(OfferState.reason_code))).all())
        total_answers = sum(number for _, number in answers)
        reason_counts = dict(answers)
        # Attribute introductory purchases created after a new-campaign impression.
        viewed = select(Impression.id).where(Impression.user_id == Payment.user_id,
            Impression.campaign == CAMPAIGN, Impression.shown_at >= since, Impression.payment_cursor < Payment.id).exists()
        payments = list((await session.execute(select(Payment.user_id, Payment.amount_rub, Payment.promo_code).where(
            Payment.status == "succeeded", Payment.plan == "trial7", Payment.amount_rub == 1, Payment.checkout_data.is_not(None), Payment.amount_rub > 0,
            Payment.plan.not_in(("gift", "admin_grant", "referral", "org")), viewed))).all())
        paid_users = {uid for uid, _, _ in payments}
        recent = list((await session.execute(select(OfferState, User, Impression)
            .join(User, User.id == OfferState.user_id).join(Impression, Impression.id == OfferState.answer_impression_id)
            .where(Impression.campaign == CAMPAIGN, Impression.shown_at >= since).order_by(OfferState.answered_at.desc()).limit(100))).all())
        daily = {}
        for column, label in ((Impression.shown_at, "shown"), (Impression.clicked_at, "tariffs")):
            day = func.date(column + text("INTERVAL 3 HOUR"))
            rows = (await session.execute(select(day, func.count()).where(Impression.campaign == CAMPAIGN, Impression.shown_at >= since,
                column.is_not(None)).group_by(day))).all()
            for key, value in rows:
                daily.setdefault(str(key), {})[label] = value
        day = func.date(OfferState.answered_at + text("INTERVAL 3 HOUR"))
        rows = (await session.execute(select(day, func.count()).join(Impression, Impression.id == OfferState.answer_impression_id)
            .where(Impression.campaign == CAMPAIGN, Impression.shown_at >= since).group_by(day))).all()
        for key, value in rows:
            daily.setdefault(str(key), {})["answers"] = value
        return {"days": days, "timezone": "Europe/Moscow", "campaign": CAMPAIGN,
            "impressions": count, "users_shown": users, "declines": declines, "dismissals": closes,
            "tariff_clicks": clicks, "users_clicked": click_users, "answers": total_answers,
            "answer_rate": round(100 * total_answers / users, 1) if users else 0,
            "click_rate": round(100 * click_users / users, 1) if users else 0,
            "paid_users": len(paid_users), "revenue_rub": float(sum(amount for _, amount, _ in payments)),
            "reasons": [{"code": code, "label": label, "count": reason_counts.get(code, 0),
                         "percent": round(100 * reason_counts.get(code, 0) / total_answers, 1) if total_answers else 0}
                        for code, label in REASONS.items()],
            "daily": [{"date": str(today - timedelta(days=offset)), "shown": 0, "tariffs": 0, "answers": 0,
                       **daily.get(str(today - timedelta(days=offset)), {})} for offset in reversed(range(days))],
            "recent_answers": [{"user_id": user.id, "name": user.first_name or user.username or f"Пользователь #{user.id}",
                "reason": state.reason_code, "reason_label": REASONS.get(state.reason_code, "Другое"),
                "answered_at": state.answered_at.isoformat() + "Z", "clicked_tariffs": bool(impression.clicked_at),
                "paid": user.id in paid_users} for state, user, impression in recent]}

"""Subscription checks shared by guided lessons and standalone practice."""
from fastapi import HTTPException
from sqlalchemy import select

from .learning_models import LearningLesson


async def require_subscription(repo, account):
    if not await repo.has_full_access(account):
        raise HTTPException(402, 'subscription_required')


async def access(repo, account):
    full = await repo.has_full_access(account)
    # Keep the access payload compatible with previously installed clients.
    return {
        'full_access': full, 'free_lesson_available': False, 'free_lesson_id': None,
        'trial_active': False, 'trial_until': None,
        'daily_limit': None if full else 0, 'used_today': 0,
        'remaining_today': None if full else 0, 'resets_at': None, 'conversation_seconds': None,
    }


async def conversation_budget(repo, account, lesson, seconds=0):
    await require_subscription(repo, account)
    return True, 0, 0, None


async def require_practice(repo, account, lesson_id=None):
    """A lesson-scoped request must also belong to an active lesson."""
    if account.is_blocked:
        raise HTTPException(403, 'account_blocked')
    if not lesson_id:
        await require_subscription(repo, account)
        return
    lesson = await repo.s.scalar(select(LearningLesson).where(
        LearningLesson.id == lesson_id, LearningLesson.user_id == account.id))
    if lesson is None:
        raise HTTPException(404, 'learning_not_found')
    await require_subscription(repo, account)
    if lesson.status != 'active':
        raise HTTPException(409, 'lesson_not_active')

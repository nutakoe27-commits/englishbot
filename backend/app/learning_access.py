"""One lifetime introductory lesson; standalone practice requires full access."""
from fastapi import HTTPException
from sqlalchemy import select

from .config import settings
from .learning_models import LearningLesson, LearningProfile


async def full_access(repo, account):
    return bool(settings.FREE_PERIOD or await repo.has_full_access(account))


async def require_standalone(repo, account):
    if not await full_access(repo, account):
        raise HTTPException(402, 'subscription_required')


async def require_lesson(repo, account, lesson):
    if await full_access(repo, account):
        return
    # Ordinals are unique per account and never reset with level/goal changes.
    if lesson.ordinal != 1 or lesson.status == 'completed':
        raise HTTPException(402, 'subscription_required')


async def access(repo, account):
    from .db.models import User
    account = await repo.s.get(User, account.id, with_for_update=True, populate_existing=True)
    full = await full_access(repo, account)
    profile = await repo.s.get(LearningProfile, account.id, with_for_update=True, populate_existing=True)
    first = await repo.s.scalar(select(LearningLesson).where(
        LearningLesson.user_id == account.id, LearningLesson.ordinal == 1).with_for_update().execution_options(populate_existing=True))
    available = not ((first and first.status == 'completed') or (profile and profile.completed_count > 0))
    return {
        'full_access': full,
        'free_lesson_available': bool(available),
        'free_lesson_id': first.id if first and available else None,
        'trial_active': False, 'trial_until': None,
        # Compatibility fields: these no longer reset daily.
        'daily_limit': None if full else 1,
        'used_today': 0 if available else 1,
        'remaining_today': None if full else int(available),
        'resets_at': None, 'conversation_seconds': None,
    }


async def conversation_budget(repo, account, lesson, seconds=0):
    await require_lesson(repo, account, lesson)
    # The complete first lesson includes speaking without a daily cutoff.
    return True, 0, 0, None


async def require_practice(repo, account, lesson_id=None):
    """Audio/translation helpers are available inside an owned active lesson."""
    if account.is_blocked:
        raise HTTPException(403, 'account_blocked')
    if not lesson_id:
        await require_standalone(repo, account)
        return
    lesson = await repo.s.scalar(select(LearningLesson).where(
        LearningLesson.id == lesson_id, LearningLesson.user_id == account.id))
    if lesson is None:
        raise HTTPException(404, 'learning_not_found')
    await require_lesson(repo, account, lesson)
    if lesson.status != 'active':
        raise HTTPException(409, 'lesson_not_active')

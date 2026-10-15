"""Shared course access rules. Practice engines remain in their existing modules."""
from datetime import datetime, timedelta

from .config import settings
from .db.repo import MSK, Repo
from . import learning_modules as modules


def budget_day():
    today = datetime.now(MSK).date()
    reset = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=MSK)
    return today.isoformat(), reset.isoformat()


async def access(repo, account):
    full = settings.FREE_PERIOD or await repo.has_full_access(account)
    daily_limit = max(0, await repo.get_kv_int('free_lessons_per_day', 1))
    conversation = max(1, await repo.get_kv_int('free_lesson_conversation_seconds', 300))
    used = await repo.count_sessions_today(account.id, 'lesson')
    trial = repo.in_free_trial(account)
    trial_until = repo.free_trial_until(account)
    return {
        'full_access': bool(full),
        'trial_active': trial,
        'trial_until': trial_until.isoformat() + 'Z' if trial_until else None,
        'daily_limit': None if full or daily_limit == 0 else daily_limit,
        'used_today': used,
        'remaining_today': None if full or daily_limit == 0 else max(0, daily_limit - used),
        'resets_at': budget_day()[1],
        'conversation_seconds': None if full else conversation,
    }


async def conversation_budget(repo, account, lesson, seconds=0):
    full = settings.FREE_PERIOD or await repo.has_full_access(account)
    cap = max(1, await repo.get_kv_int('free_lesson_conversation_seconds', 300))
    day, reset = budget_day()
    previous = modules.result(lesson, 'conversation').get('free_usage', {})
    used = int(previous.get('seconds', 0)) if previous.get('day') == day else 0
    if seconds and not full:
        used += seconds
        modules.save(lesson, 'conversation', free_usage={'day': day, 'seconds': used})
    return bool(full), cap, used, reset

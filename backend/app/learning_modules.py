"""Course orchestration around the existing grammar, SRS, podcast and tutor modes.

The modes own exercises, audio, review scheduling and usage accounting. The
course only records their durable results and controls the order of stages.
"""
from fastapi import HTTPException
from .config import settings
from .learning_models import LearningLesson
from sqlalchemy import select

STAGES = ('grammar', 'mistakes', 'words', 'listening', 'conversation')


def grade_grammar(session, activity, key, answers):
    if not key:
        return sum(answer.is_correct for answer in answers), len(answers)
    from .db.models import UserMistake
    from .grammar_sessions import now
    prepared = activity.grammar_result or {}
    exercises = prepared.get('_exercises', [])
    if prepared.get('_learning_lesson_id') != key:
        raise HTTPException(409, 'module_session_mismatch')
    submitted = {answer.exercise_id: answer.user_answer for answer in answers}
    if not exercises or len(submitted) != len(exercises) or set(submitted) != {ex['id'] for ex in exercises}:
        raise HTTPException(409, 'grammar_answers_required')
    correct = 0
    for exercise in exercises:
        answer = submitted[exercise['id']].strip()
        expected = exercise['correct'].strip()
        if answer.casefold() == expected.casefold():
            correct += 1
        else:
            session.add(UserMistake(user_id=activity.user_id,category=exercise['category'],
                bad_phrase=exercise['prompt'].replace('___', answer)[:255],
                good_phrase=exercise['prompt'].replace('___', expected)[:255],occurred_at=now()))
    return correct, len(exercises)


async def require(session, uid, key, stage, *, finishing=False):
    if not key:
        return None
    if not settings.LEARNING_ENABLED:
        raise HTTPException(404, 'learning_disabled')
    from .db.models import User
    account = await session.get(User, uid, with_for_update=True, populate_existing=True)
    if account is None:
        raise HTTPException(401, 'user_not_found')
    if account.is_blocked:
        raise HTTPException(403, 'account_blocked')
    row = await session.scalar(select(LearningLesson).where(
        LearningLesson.id == key, LearningLesson.user_id == uid).with_for_update())
    if row is None:
        raise HTTPException(404, 'learning_not_found')
    if row.content.get('engine') != 'modules':
        raise HTTPException(409, 'lesson_engine_mismatch')
    if row.step_index != STAGES.index(stage):
        raise HTTPException(409, 'step_out_of_order')
    if row.status not in (('active', 'paused') if finishing else ('active',)):
        raise HTTPException(409, 'lesson_not_active')
    return row


def result(row, stage):
    return row.answers.get('_modules', {}).get(stage, {}) if row else {}


def save(row, stage, **values):
    if row is not None:
        modules = dict(row.answers.get('_modules', {}))
        modules[stage] = {**modules.get(stage, {}), **values}
        row.answers = {**row.answers, '_modules': modules}


async def grammar_completed(session, uid, key, activity, *, correct, total):
    stage = 'mistakes' if activity.role == 'weak_points' else 'grammar'
    row = await require(session, uid, key, stage, finishing=True)
    if row is None:
        return
    # Preparation activation binds the session to this course stage.
    if str(activity.id) != str(result(row, stage).get('session_id')):
        raise HTTPException(409, 'module_session_mismatch')
    save(row, stage, completed=True, correct=correct, total=total,
         seconds=activity.used_seconds or 0)


async def conversation_open(session, uid, key, activity):
    row = await require(session, uid, key, 'conversation')
    save(row, 'conversation', session_id=str(activity.id), completed=False)


async def conversation_closed(session, uid, key, activity_id, history, seconds):
    if not key: return
    row = await session.scalar(select(LearningLesson).where(
        LearningLesson.id==key,LearningLesson.user_id==uid).with_for_update())
    if row is None or row.step_index!=4 or row.status not in ('active','paused'): return
    previous = result(row, 'conversation')
    if str(activity_id) != str(previous.get('session_id')):
        return  # A late close from an older connection must not finish a new one.
    turns = sum(item.get('role') == 'user' for item in history)
    save(row, 'conversation', completed=turns >= 3, turns=turns,
         seconds=previous.get('seconds', 0) + seconds, history=history[-40:])

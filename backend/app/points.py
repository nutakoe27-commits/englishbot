"""
points.py — очки и уровни (геймификация).

Очки начисляются за активность:
    - 1 очко за каждую минуту разговора (voice / chat)
    - 1 очко за каждую минуту прослушанного подкаста (listening)
    - 5 очков за каждый завершённый урок/тест грамматики (1 grammar-сессия)

Очки считаются НА ЛЕТУ из таблицы sessions (см. repo.user_points) — отдельной
таблицы нет. Лидерборд использует месячные очки, уровень — lifetime.

Уровни: порог между уровнем L и L+1 = 25·L очков (каждый следующий на 25
больше). Кумулятивный минимум очков, чтобы БЫТЬ уровнем L:
    min(L) = 25 · (L-1) · L / 2
    L1=0, L2=25, L3=75, L4=150, L5=250, …
"""

from __future__ import annotations

GRAMMAR_POINTS = 5  # очков за один завершённый урок/тест грамматики


def compute_points(speaking_seconds: int, listening_seconds: int, grammar_sessions: int) -> int:
    """Очки = минуты говорения + минуты слушания + 5·(уроки грамматики)."""
    return (
        speaking_seconds // 60
        + listening_seconds // 60
        + grammar_sessions * GRAMMAR_POINTS
    )


def _level_floor(level: int) -> int:
    """Минимум lifetime-очков, чтобы быть на уровне `level` (level ≥ 1)."""
    if level <= 1:
        return 0
    n = level - 1
    return 25 * n * (level) // 2  # 25·(L-1)·L/2


def level_info(lifetime_points: int) -> dict:
    """По lifetime-очкам считает уровень и прогресс до следующего.

    Возвращает:
        level            — текущий уровень (≥1)
        lifetime_points  — переданные очки
        level_floor      — мин. очков текущего уровня
        next_floor       — очков для следующего уровня
        into_level       — сколько очков набрано внутри текущего уровня
        level_span       — сколько очков нужно на весь текущий уровень (25·level)
        progress_pct     — 0..100, прогресс до следующего уровня
    """
    p = max(0, int(lifetime_points))
    # Ищем максимальный level, где _level_floor(level) <= p.
    level = 1
    while _level_floor(level + 1) <= p:
        level += 1
    floor = _level_floor(level)
    span = 25 * level  # очков чтобы перейти на level+1
    next_floor = floor + span
    into = p - floor
    pct = int(round((into / span) * 100)) if span > 0 else 0
    pct = max(0, min(100, pct))
    return {
        "level": level,
        "lifetime_points": p,
        "level_floor": floor,
        "next_floor": next_floor,
        "into_level": into,
        "level_span": span,
        "progress_pct": pct,
    }

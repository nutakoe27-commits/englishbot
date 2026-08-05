"""Тарификация B2B-пакетов для школ.

Единственный источник истины по цене. Фронт дублирует формулу для мгновенного
отклика калькулятора, но сумма платежа ВСЕГДА пересчитывается здесь — данным
из браузера не доверяем.

Модель: базовая цена за место в месяц, скидка за объём + скидка за срок
(скидки складываются).
"""

from __future__ import annotations

BASE_PRICE_PER_SEAT_MONTH = 590   # ₽ за одно место в месяц без скидок
MIN_SEATS = 10                    # минимальный заказ
MAX_SEATS = 5000                  # предохранитель от опечаток
ALLOWED_MONTHS = (3, 6, 12)

# Скидка за объём: (порог мест, процент)
VOLUME_TIERS = ((100, 20), (30, 10))
# Скидка за срок: (месяцев, процент)
PERIOD_TIERS = ((12, 15), (6, 5))


def volume_discount(seats: int) -> int:
    for threshold, pct in VOLUME_TIERS:
        if seats >= threshold:
            return pct
    return 0


def period_discount(months: int) -> int:
    for threshold, pct in PERIOD_TIERS:
        if months >= threshold:
            return pct
    return 0


def validate(seats: int, months: int) -> tuple[int, int]:
    """Нормализует и проверяет параметры заказа. Кидает ValueError."""
    try:
        seats = int(seats)
        months = int(months)
    except (TypeError, ValueError):
        raise ValueError("bad_params")
    if seats < MIN_SEATS or seats > MAX_SEATS:
        raise ValueError("bad_seats")
    if months not in ALLOWED_MONTHS:
        raise ValueError("bad_months")
    return seats, months


def quote(seats: int, months: int) -> dict:
    """Расчёт стоимости пакета. Возвращает разбивку для калькулятора и чека."""
    seats, months = validate(seats, months)
    vol = volume_discount(seats)
    per = period_discount(months)
    total_discount = vol + per

    base_total = seats * months * BASE_PRICE_PER_SEAT_MONTH
    total = int(round(base_total * (100 - total_discount) / 100))
    # Цена за место в месяц со скидкой — то, что школа сравнивает с розницей.
    per_seat_month = int(round(total / (seats * months)))

    return {
        "seats": seats,
        "months": months,
        "base_price_per_seat_month": BASE_PRICE_PER_SEAT_MONTH,
        "volume_discount_pct": vol,
        "period_discount_pct": per,
        "total_discount_pct": total_discount,
        "base_total_rub": base_total,
        "total_rub": total,
        "per_seat_month_rub": per_seat_month,
        "saving_rub": base_total - total,
        # Дней доступа: месяц считаем как 30 дней — так же, как в личных тарифах.
        "days": months * 30,
    }


def limits() -> dict:
    """Параметры для калькулятора на фронте."""
    return {
        "base_price_per_seat_month": BASE_PRICE_PER_SEAT_MONTH,
        "min_seats": MIN_SEATS,
        "max_seats": MAX_SEATS,
        "allowed_months": list(ALLOWED_MONTHS),
        "volume_tiers": [{"seats": s, "pct": p} for s, p in VOLUME_TIERS],
        "period_tiers": [{"months": m, "pct": p} for m, p in PERIOD_TIERS],
    }

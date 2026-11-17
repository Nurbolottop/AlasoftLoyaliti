"""Часы работы заведения: проверка формата и «открыто сейчас» (BACKEND_TZ 3.2).

Формат: ``[{"day": 1, "open": "10:00", "close": "22:00", "is_closed": false}]``,
``day`` 1 — понедельник … 7 — воскресенье. Закрытие после полуночи
(``22:00–02:00``) допустимо: такой интервал продолжается в следующий день.
"""

import math
import re
from datetime import datetime, time, timedelta

from django.utils import timezone

_TIME_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')


def _parse(value):
    match = _TIME_RE.match(value or '')
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def normalize_working_hours(value):
    """Проверяет и приводит часы к каноническому виду; ValueError с текстом ошибки."""
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValueError('Ожидается список дней')
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError('Каждый день — объект {day, open, close, is_closed}')
        try:
            day = int(entry.get('day'))
        except (TypeError, ValueError):
            raise ValueError('day — число от 1 до 7')
        if not 1 <= day <= 7:
            raise ValueError('day — число от 1 до 7')
        if day in seen:
            raise ValueError(f'День {day} указан дважды')
        seen.add(day)
        is_closed = bool(entry.get('is_closed', False))
        open_, close = (entry.get('open') or '').strip(), (entry.get('close') or '').strip()
        if not is_closed and (_parse(open_) is None or _parse(close) is None):
            raise ValueError(f'День {day}: время в формате ЧЧ:ММ')
        result.append({
            'day': day,
            'open': '' if is_closed else open_,
            'close': '' if is_closed else close,
            'is_closed': is_closed,
        })
    return sorted(result, key=lambda item: item['day'])


def _intervals(hours, local_date):
    """Интервалы работы, затрагивающие эту дату: свой день и хвост вчерашнего."""
    by_day = {item.get('day'): item for item in hours or []}
    intervals = []
    for offset in (-1, 0):
        date = local_date + timedelta(days=offset)
        item = by_day.get(date.isoweekday())
        if not item or item.get('is_closed'):
            continue
        open_t, close_t = _parse(item.get('open')), _parse(item.get('close'))
        if open_t is None or close_t is None:
            continue
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(date, open_t), tz)
        end_date = date if close_t > open_t else date + timedelta(days=1)
        end = timezone.make_aware(datetime.combine(end_date, close_t), tz)
        intervals.append((start, end))
    return intervals


def is_open_now(hours, now=None):
    """None — часы не заданы, иначе True/False."""
    if not hours:
        return None
    now = timezone.localtime(now or timezone.now())
    return any(start <= now < end for start, end in _intervals(hours, now.date()))


def today_hours(hours, now=None):
    """Часы сегодняшнего дня для подписи «Сегодня: 10:00–22:00»."""
    if not hours:
        return None
    now = timezone.localtime(now or timezone.now())
    for item in hours:
        if item.get('day') == now.isoweekday():
            return item
    return None


def distance_km(lat1, lng1, lat2, lng2):
    """Расстояние по прямой (гаверсинус), км с точностью до 0,1."""
    if None in (lat1, lng1, lat2, lng2):
        return None
    lat1, lng1, lat2, lng2 = map(lambda v: math.radians(float(v)), (lat1, lng1, lat2, lng2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return round(6371.0 * 2 * math.asin(math.sqrt(a)), 1)

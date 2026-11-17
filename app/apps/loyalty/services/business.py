"""Отчёты TOPTO Business: главная, выручка, оплаты, результаты сотрудника.

Период ``N`` дней — сегодня и N−1 предыдущих календарных дней по времени
Бишкека (BACKEND_TZ §2). Выручка — сумма ``cash_paid`` завершённых
операций: у каждой оплаты он заполнен ровно у одной операции, отменённые не
считаются. Поэтому revenue из overview = total из revenue = сумма amount в
payments = сумма sales_total по сотрудникам.
"""

from datetime import datetime, time, timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from apps.common.errors import DomainError, ErrorCode
from apps.loyalty.models import (
    RedemptionRequest,
    RedemptionStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
    UserOrganizationState,
)
from apps.organizations.models import LoyaltyType

ALLOWED_PERIODS = (1, 7, 15, 30)
# Операции, после которых клиент считается «посетившим» заведение
VISIT_TYPES = (
    TransactionType.VISIT_EARN, TransactionType.GIFT_REDEEM,
    TransactionType.CASHBACK_EARN, TransactionType.CASHBACK_SPEND,
)
# Точки графика выручки за день: каждые 2 часа с 09:00 до 21:00
DAY_POINT_HOURS = (9, 11, 13, 15, 17, 19, 21)


def parse_period(value, default=1):
    if value in (None, ''):
        return default
    try:
        period = int(value)
    except (TypeError, ValueError):
        period = None
    if period not in ALLOWED_PERIODS:
        raise DomainError(
            code=ErrorCode.VALIDATION_ERROR,
            message='period — одно из значений 1, 7, 15, 30',
            status_code=400,
            details={'period': list(ALLOWED_PERIODS)},
        )
    return period


def period_bounds(period, now=None):
    """[start, end) периода и такого же предыдущего."""
    now = timezone.localtime(now or timezone.now())
    tz = timezone.get_current_timezone()
    today = now.date()
    start = timezone.make_aware(datetime.combine(today - timedelta(days=period - 1), time.min), tz)
    end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), tz)
    previous_start = start - timedelta(days=period)
    return start, end, previous_start


def completed(organization):
    return Transaction.objects.filter(organization=organization, status=TransactionStatus.COMPLETED)


def payments(organization, start, end, employee=None):
    queryset = completed(organization).filter(
        cash_paid__isnull=False, created_at__gte=start, created_at__lt=end,
    )
    if employee is not None:
        queryset = queryset.filter(employee=employee)
    return queryset


def _sum(queryset, field):
    return int(queryset.aggregate(total=Sum(field))['total'] or 0)


def members(organization):
    return UserOrganizationState.objects.filter(organization=organization, joined_at__isnull=False)


def free_cards(queryset):
    """Сколько раз карта заполнилась: отметки, после которых выдан подарок."""
    return queryset.filter(
        type=TransactionType.VISIT_EARN, metadata__gifts_created__0__isnull=False,
    ).count()


def overview(organization, period, now=None):
    start, end, _ = period_bounds(period, now)
    in_period = completed(organization).filter(created_at__gte=start, created_at__lt=end)

    if organization.loyalty_type == LoyaltyType.CASHBACK:
        bonuses = _sum(in_period.filter(type=TransactionType.CASHBACK_EARN), 'amount')
    else:
        bonuses = in_period.filter(type=TransactionType.GIFT_REDEEM).count()

    return {
        'period': period,
        'customers_total': members(organization).count(),
        'visitors': in_period.filter(type__in=VISIT_TYPES).values('user_id').distinct().count(),
        'new_customers': members(organization).filter(joined_at__gte=start, joined_at__lt=end).count(),
        'bonuses_issued': bonuses,
        'revenue': _sum(payments(organization, start, end), 'cash_paid'),
        'pending_redemptions': RedemptionRequest.objects.filter(
            organization=organization, status=RedemptionStatus.PENDING,
        ).count(),
    }


def revenue(organization, period, now=None):
    start, end, previous_start = period_bounds(period, now)
    current = payments(organization, start, end)
    total = _sum(current, 'cash_paid')
    paid_visits = current.count()
    in_period = completed(organization).filter(created_at__gte=start, created_at__lt=end)
    tz = timezone.get_current_timezone()

    if period == 1:
        # Точка в HH:00 — выручка с HH:00 до следующей точки; первая точка
        # забирает утро до 09:00, последняя — вечер до полуночи.
        day = timezone.localtime(start).date()
        edges = [timezone.make_aware(datetime.combine(day, time(hour)), tz) for hour in DAY_POINT_HOURS]
        points = []
        for index, edge in enumerate(edges):
            left = start if index == 0 else edge
            right = edges[index + 1] if index + 1 < len(edges) else end
            points.append({'at': edge, 'amount': _sum(current.filter(created_at__gte=left, created_at__lt=right),
                                                      'cash_paid')})
    else:
        points = []
        for offset in range(period):
            left = start + timedelta(days=offset)
            right = left + timedelta(days=1)
            points.append({'at': left, 'amount': _sum(current.filter(created_at__gte=left, created_at__lt=right),
                                                      'cash_paid')})

    # Средний чек — до сома (сумма в тыйынах кратна 100)
    average = round(total / paid_visits / 100) * 100 if paid_visits else 0

    return {
        'period': period,
        'loyalty_type': organization.loyalty_type,
        'total': total,
        'previous_total': _sum(payments(organization, previous_start, start), 'cash_paid'),
        'points': points,
        'paid_visits': paid_visits,
        'customers_total': members(organization).count(),
        'bonuses_earned': _sum(in_period.filter(type=TransactionType.CASHBACK_EARN), 'amount'),
        'average_check': average,
        'free_cards': free_cards(in_period),
        'free_visits': in_period.filter(type=TransactionType.GIFT_REDEEM).count(),
    }


def employee_stats(employee, period, now=None):
    """Результаты работы сотрудника (7.11). Отменённые операции не считаются."""
    organization = employee.organization
    start, end, _ = period_bounds(period, now)
    own = completed(organization).filter(employee=employee, created_at__gte=start, created_at__lt=end)
    own_payments = payments(organization, start, end, employee=employee)

    served_ids = set(own.filter(type__in=VISIT_TYPES).values_list('user_id', flat=True))
    # Новый клиент сотрудника: первая операция покупателя в заведении — его
    new_clients = 0
    for user_id in served_ids:
        first = (
            completed(organization).filter(user_id=user_id, type__in=VISIT_TYPES)
            .order_by('created_at', 'id').values('employee_id', 'created_at').first()
        )
        if first and first['employee_id'] == employee.id and start <= first['created_at'] < end:
            new_clients += 1

    return {
        'period': period,
        'sales_total': _sum(own_payments, 'cash_paid'),
        'paid_visits': own_payments.count(),
        'completed_cards': free_cards(own),
        'rewards_issued': own.filter(type=TransactionType.GIFT_REDEEM).count(),
        'bonuses_given': _sum(own.filter(type=TransactionType.CASHBACK_EARN), 'amount'),
        'clients_served': len(served_ids),
        'new_clients': new_clients,
    }


def last_operations(organization, user_ids):
    """Последняя операция каждого клиента (для списков клиентов)."""
    result = {}
    queryset = (
        Transaction.objects.filter(organization=organization, user_id__in=list(user_ids))
        .exclude(type=TransactionType.GIFT_CREATED)
        .select_related('organization', 'organization__category')
        .order_by('user_id', '-created_at', '-id')
        .distinct('user_id')
    )
    for tx in queryset:
        result[tx.user_id] = tx
    return result


def customers_queryset(organization, params, now=None):
    """Клиенты заведения с фильтрами 7.3."""
    now = timezone.localtime(now or timezone.now())
    queryset = members(organization).select_related('user')

    search = (params.get('search') or '').strip()
    if len(search) >= 2:
        digits = ''.join(ch for ch in search if ch.isdigit())
        condition = (
            Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search)
        )
        if digits:
            condition |= Q(user__phone__contains=digits) | Q(user__public_code=digits)
        queryset = queryset.filter(condition)

    flt = params.get('filter') or 'all'
    if flt == 'today':
        start, _, _ = period_bounds(1, now)
        queryset = queryset.filter(last_activity_at__gte=start)
    elif flt == 'new':
        queryset = queryset.filter(joined_at__gte=now - timedelta(days=30))
    elif flt == 'with_bonuses':
        queryset = queryset.filter(Q(cashback_available__gt=0) | Q(available_gifts__gt=0))

    gender = params.get('gender')
    if gender in ('MALE', 'FEMALE'):
        queryset = queryset.filter(user__gender=gender)

    from django.utils.dateparse import parse_date
    tz = timezone.get_current_timezone()
    visited_from = parse_date(params.get('visited_from') or '') if params.get('visited_from') else None
    visited_to = parse_date(params.get('visited_to') or '') if params.get('visited_to') else None
    if visited_from:
        queryset = queryset.filter(
            last_activity_at__gte=timezone.make_aware(datetime.combine(visited_from, time.min), tz))
    if visited_to:
        queryset = queryset.filter(
            last_activity_at__lt=timezone.make_aware(datetime.combine(visited_to + timedelta(days=1), time.min), tz))

    if params.get('ordering') == 'full_name':
        return queryset.order_by('user__first_name', 'user__last_name')
    from django.db.models import F
    return queryset.order_by(F('last_activity_at').desc(nulls_last=True), '-joined_at')

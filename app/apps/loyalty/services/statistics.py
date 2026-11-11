"""Статистика организации и платформы (ТЗ общее §17)."""

from datetime import datetime, time, timedelta

from django.db.models import BigIntegerField, Count, Q, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Gift,
    GiftStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
    UserOrganizationState,
)
from apps.organizations.models import LoyaltyType, Organization, OrganizationStatus
from apps.users.models import Role, User


MAX_PERIOD_DAYS = 366


def _period(date_from=None, date_to=None, default_days=30):
    now = timezone.now()
    date_to = date_to or now
    date_from = date_from or (now - timedelta(days=default_days))
    return date_from, date_to


def day_bound(value, *, end=False):
    """'YYYY-MM-DD' (можно с хвостом времени) → начало/конец дня в поясе проекта."""
    try:
        day = parse_date((value or '')[:10])
    except ValueError:
        day = None
    if day is None:
        return None
    return timezone.make_aware(
        datetime.combine(day, time.max if end else time.min), timezone.get_current_timezone()
    )


def period_from_params(params):
    """Период из query (?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD) для панели.

    Даты включительные. Пустые или невалидные значения дают период по
    умолчанию (последние 30 дней); слишком длинный период обрезается до
    года, чтобы дневной ряд не разрастался.
    """
    date_from = day_bound(params.get('date_from'), end=False)
    date_to = day_bound(params.get('date_to'), end=True)
    if not date_from and not date_to:
        return None, None
    date_to = date_to or timezone.now()
    date_from = date_from or date_to - timedelta(days=30)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        date_from = date_to - timedelta(days=MAX_PERIOD_DAYS)
    return date_from, date_to


def _days(date_from, date_to):
    tz = timezone.get_current_timezone()
    day = timezone.localtime(date_from, tz).date()
    last = timezone.localtime(date_to, tz).date()
    result = []
    while day <= last:
        result.append(day)
        day += timedelta(days=1)
    return result


def _per_day(queryset, field, value=None):
    """{date: count|sum} по дням в часовом поясе проекта."""
    rows = queryset.order_by().annotate(day=TruncDate(field)).values('day')
    rows = rows.annotate(v=Sum(value) if value else Count('id'))
    return {row['day']: int(row['v'] or 0) for row in rows}


def _turnover(transactions):
    """Сумма чеков по начислениям cashback (purchase_total в metadata)."""
    return int(
        transactions.annotate(
            purchase=Cast(KeyTextTransform('purchase_total', 'metadata'), BigIntegerField())
        ).aggregate(s=Sum('purchase'))['s'] or 0
    )


def _tx_series(tx, days):
    completed = tx.filter(status=TransactionStatus.COMPLETED)
    by_type = {
        'visits': _per_day(completed.filter(type=TransactionType.VISIT_EARN), 'created_at'),
        'cashback_earned': _per_day(
            completed.filter(type=TransactionType.CASHBACK_EARN), 'created_at', 'amount'),
        'cashback_spent': _per_day(
            completed.filter(type=TransactionType.CASHBACK_SPEND), 'created_at', 'amount'),
        'operations': _per_day(tx, 'created_at'),
    }
    return [
        {'date': day.isoformat(), **{key: values.get(day, 0) for key, values in by_type.items()}}
        for day in days
    ]


def organization_statistics(organization, *, date_from=None, date_to=None, with_series=False):
    date_from, date_to = _period(date_from, date_to)
    tx = Transaction.objects.filter(
        organization=organization, created_at__gte=date_from, created_at__lte=date_to
    )
    completed = tx.filter(status=TransactionStatus.COMPLETED)
    states = UserOrganizationState.objects.filter(organization=organization)

    common = {
        'organization_id': str(organization.id),
        'loyalty_type': organization.loyalty_type,
        'period': {'from': date_from, 'to': date_to},
        'customers_total': states.count(),
        'customers_active': states.filter(last_activity_at__gte=date_from).count(),
        'customers_new': states.filter(
            first_activity_at__gte=date_from, first_activity_at__lte=date_to
        ).count(),
        'reversals': tx.filter(type=TransactionType.REVERSAL).count(),
    }
    if with_series:
        common['series'] = _tx_series(tx, _days(date_from, date_to))

    if organization.loyalty_type == LoyaltyType.VISIT:
        visits = completed.filter(type=TransactionType.VISIT_EARN).count()
        repeat_customers = (
            states.filter(total_visits__gte=2).count()
        )
        common.update({
            'visits': visits,
            'gifts_created': Gift.objects.filter(
                organization=organization, created_at__gte=date_from, created_at__lte=date_to
            ).count(),
            'gifts_used': Gift.objects.filter(
                organization=organization, status=GiftStatus.USED,
                used_at__gte=date_from, used_at__lte=date_to,
            ).count(),
            'gifts_available_now': Gift.objects.filter(
                organization=organization, status=GiftStatus.AVAILABLE
            ).count(),
            'repeat_customers': repeat_customers,
        })
        return common

    earned = completed.filter(type=TransactionType.CASHBACK_EARN).aggregate(s=Sum('amount'))['s'] or 0
    spent = completed.filter(type=TransactionType.CASHBACK_SPEND).aggregate(s=Sum('amount'))['s'] or 0
    expired = completed.filter(type=TransactionType.CASHBACK_EXPIRE).aggregate(s=Sum('amount'))['s'] or 0
    turnover = _turnover(completed.filter(type=TransactionType.CASHBACK_EARN))
    active_balance = CashbackLot.objects.filter(
        organization=organization, status=CashbackLotStatus.ACTIVE,
        remaining_amount__gt=0, expires_at__gt=timezone.now(),
    ).aggregate(s=Sum('remaining_amount'))['s'] or 0

    common.update({
        'turnover': turnover,
        'cashback_earned': int(earned),
        'cashback_spent': int(spent),
        'cashback_expired': int(expired),
        'cashback_active_balance': int(active_balance),
        'repeat_customers': states.filter(cashback_total_earned__gt=0).count(),
    })
    return common


def platform_statistics(*, date_from=None, date_to=None, with_series=False):
    date_from, date_to = _period(date_from, date_to)
    organizations = Organization.objects.all()
    users = User.objects.filter(role=Role.USER)
    tx = Transaction.objects.filter(created_at__gte=date_from, created_at__lte=date_to)
    completed = tx.filter(status=TransactionStatus.COMPLETED)

    by_type = {
        row['loyalty_type']: row['total']
        for row in organizations.values('loyalty_type').annotate(total=Count('id'))
    }
    active_user_ids = tx.values('user_id').distinct().count()

    result = {
        'period': {'from': date_from, 'to': date_to},
        'organizations_total': organizations.count(),
        'organizations_active': organizations.filter(status=OrganizationStatus.ACTIVE).count(),
        'organizations_blocked': organizations.filter(status=OrganizationStatus.BLOCKED).count(),
        'organizations_by_program': {
            'VISIT': by_type.get(LoyaltyType.VISIT, 0),
            'CASHBACK': by_type.get(LoyaltyType.CASHBACK, 0),
        },
        'organizations_new_in_period': organizations.filter(
            created_at__gte=date_from, created_at__lte=date_to
        ).count(),
        'users_total': users.count(),
        'users_registered': users.filter(is_registration_complete=True).count(),
        'users_active': users.filter(is_active=True).count(),
        'users_new_in_period': users.filter(
            date_joined__gte=date_from, date_joined__lte=date_to
        ).count(),
        'users_active_in_period': active_user_ids,
        'operations_total': tx.count(),
        'operations_by_type': {
            row['type']: row['total']
            for row in tx.values('type').annotate(total=Count('id'))
        },
        'cashback_earned': int(
            tx.filter(type=TransactionType.CASHBACK_EARN, status=TransactionStatus.COMPLETED)
            .aggregate(s=Sum('amount'))['s'] or 0
        ),
        'cashback_spent': int(
            tx.filter(type=TransactionType.CASHBACK_SPEND, status=TransactionStatus.COMPLETED)
            .aggregate(s=Sum('amount'))['s'] or 0
        ),
        'cashback_expired': int(
            tx.filter(type=TransactionType.CASHBACK_EXPIRE, status=TransactionStatus.COMPLETED)
            .aggregate(s=Sum('amount'))['s'] or 0
        ),
        'gifts_created': Gift.objects.filter(created_at__gte=date_from, created_at__lte=date_to).count(),
        'gifts_used': Gift.objects.filter(
            status=GiftStatus.USED, used_at__gte=date_from, used_at__lte=date_to
        ).count(),
        'reversals': tx.filter(type=TransactionType.REVERSAL).count(),
        'turnover': _turnover(completed.filter(type=TransactionType.CASHBACK_EARN)),
        'cashback_active_balance': int(
            CashbackLot.objects.filter(
                status=CashbackLotStatus.ACTIVE, remaining_amount__gt=0,
                expires_at__gt=timezone.now(),
            ).aggregate(s=Sum('remaining_amount'))['s'] or 0
        ),
    }
    if with_series:
        result['series'] = _platform_series(tx, users, organizations, date_from, date_to)
    return result


def _platform_series(tx, users, organizations, date_from, date_to):
    """Дневной ряд платформы: операции + накопленное число клиентов и организаций."""
    days = _days(date_from, date_to)
    series = _tx_series(tx, days)
    new_users = _per_day(
        users.filter(date_joined__gte=date_from, date_joined__lte=date_to), 'date_joined')
    new_orgs = _per_day(
        organizations.filter(created_at__gte=date_from, created_at__lte=date_to), 'created_at')
    users_total = users.filter(date_joined__lt=date_from).count()
    orgs_total = organizations.filter(created_at__lt=date_from).count()
    for point, day in zip(series, days):
        users_total += new_users.get(day, 0)
        orgs_total += new_orgs.get(day, 0)
        point.update({
            'users_new': new_users.get(day, 0),
            'users_total': users_total,
            'organizations_new': new_orgs.get(day, 0),
            'organizations_total': orgs_total,
        })
    return series

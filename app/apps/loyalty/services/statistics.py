"""Статистика организации и платформы (ТЗ общее §17)."""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

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


def _period(date_from=None, date_to=None, default_days=30):
    now = timezone.now()
    date_to = date_to or now
    date_from = date_from or (now - timedelta(days=default_days))
    return date_from, date_to


def organization_statistics(organization, *, date_from=None, date_to=None):
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
        'reversals': tx.filter(type=TransactionType.REVERSAL).count(),
    }

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
    turnover = sum(
        int(t.metadata.get('purchase_total') or 0)
        for t in completed.filter(type=TransactionType.CASHBACK_EARN).only('metadata')
    )
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


def platform_statistics(*, date_from=None, date_to=None):
    date_from, date_to = _period(date_from, date_to)
    organizations = Organization.objects.all()
    tx = Transaction.objects.filter(created_at__gte=date_from, created_at__lte=date_to)

    by_type = {
        row['loyalty_type']: row['total']
        for row in organizations.values('loyalty_type').annotate(total=Count('id'))
    }
    active_user_ids = tx.values('user_id').distinct().count()

    return {
        'period': {'from': date_from, 'to': date_to},
        'organizations_total': organizations.count(),
        'organizations_active': organizations.filter(status=OrganizationStatus.ACTIVE).count(),
        'organizations_blocked': organizations.filter(status=OrganizationStatus.BLOCKED).count(),
        'organizations_by_program': {
            'VISIT': by_type.get(LoyaltyType.VISIT, 0),
            'CASHBACK': by_type.get(LoyaltyType.CASHBACK, 0),
        },
        'users_total': User.objects.filter(role=Role.USER).count(),
        'users_registered': User.objects.filter(role=Role.USER, is_registration_complete=True).count(),
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
    }

import logging

from celery import shared_task
from django.conf import settings

from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services import redemptions as redemption_service
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_user

logger = logging.getLogger('alasoft.tasks')


@shared_task(name='loyalty.expire_cashback_lots')
def expire_cashback_lots():
    """ExpireCashbackLots — идемпотентна: повтор не сгорит дважды."""
    result = cashback_service.expire_lots()
    if result['lots_processed']:
        logger.info('Сгорание cashback: %s лотов, %s тыйын', result['lots_processed'], result['amount_expired'])
    return result


@shared_task(name='loyalty.cleanup_expired_redemptions')
def cleanup_expired_redemptions():
    """CleanupExpiredRedemptions: PENDING → EXPIRED."""
    return redemption_service.expire_pending()


@shared_task(name='loyalty.notify_expiring_cashback')
def notify_expiring_cashback():
    """CASHBACK_EXPIRING за 7 дней и за 1 день до сгорания (BACKEND_TZ 8, 9.4).

    Запускается раз в день: берём партии, которые сгорят ровно через N
    календарных дней, — так одно предупреждение не повторяется каждый день.
    """
    from datetime import datetime, time, timedelta

    from django.utils import timezone

    from apps.loyalty.models import CashbackLot, CashbackLotStatus

    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    sent = 0
    for days in settings.CASHBACK_EXPIRY_WARNING_SCHEDULE:
        start = timezone.make_aware(datetime.combine(today + timedelta(days=days), time.min), tz)
        lots = CashbackLot.objects.filter(
            status=CashbackLotStatus.ACTIVE, remaining_amount__gt=0,
            expires_at__gte=start, expires_at__lt=start + timedelta(days=1),
        ).select_related('user', 'organization')
        for lot in lots:
            notify_user(
                lot.user, NotificationEvent.CASHBACK_EXPIRING, organization=lot.organization,
                context={'amount_tiyin': lot.remaining_amount, 'days': days,
                         'expires_at': lot.expires_at.isoformat()},
            )
            sent += 1
    return {'notified': sent}


@shared_task(name='loyalty.daily_summary')
def daily_summary():
    """Итоги дня администраторам заведений (DAILY_SUMMARY, 9.4)."""
    from apps.loyalty.services import business
    from apps.notifications.services import notify_organization_admins
    from apps.organizations.models import Organization, OrganizationStatus

    sent = 0
    for organization in Organization.objects.filter(status=OrganizationStatus.ACTIVE):
        data = business.overview(organization, 1)
        if not (data['revenue'] or data['visitors'] or data['new_customers']):
            continue
        notify_organization_admins(organization, NotificationEvent.DAILY_SUMMARY, context={
            'revenue_tiyin': data['revenue'], 'visitors': data['visitors'],
            'new_customers': data['new_customers'],
        })
        sent += 1
    return {'organizations': sent}

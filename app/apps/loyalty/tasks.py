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
    """Предупреждение о скором сгорании (ТЗ общее §15)."""
    days = settings.CASHBACK_EXPIRY_WARNING_DAYS
    sent = 0
    for lot in cashback_service.lots_expiring_soon(days=days):
        notify_user(
            lot.user, NotificationEvent.CASHBACK_EXPIRING, organization=lot.organization,
            context={'amount_tiyin': lot.remaining_amount, 'days': days},
        )
        sent += 1
    return {'notified': sent}

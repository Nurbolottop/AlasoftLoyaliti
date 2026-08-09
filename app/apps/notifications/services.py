"""Постановка уведомлений в очередь.

Ошибка отправки push/SMS никогда не откатывает бизнес-транзакцию
(ТЗ backend §25): задачи ставятся только после успешного commit.
"""

import logging

from django.db import transaction

from apps.notifications.models import Notification, NotificationChannel, NotificationEvent

logger = logging.getLogger('alasoft.notifications')


def notify_user(user, event, *, organization=None, context=None, language=None):
    """Ставит push в очередь после commit текущей транзакции."""
    if user is None:
        return None

    context = dict(context or {})
    if organization is not None:
        context.setdefault('organization', organization.name)
        context.setdefault('organization_id', str(organization.id))

    language = language or getattr(user, 'language', 'ru')
    notification = Notification.objects.create(
        user=user,
        organization=organization,
        channel=NotificationChannel.PUSH,
        event=event,
        language=language,
        payload=context,
    )

    def _dispatch():
        from apps.notifications.tasks import send_push_notification
        try:
            send_push_notification.delay(str(notification.id))
        except Exception:
            logger.exception('Не удалось поставить push %s в очередь', notification.id)

    transaction.on_commit(_dispatch)
    return notification


def send_sms(phone, event, *, context=None, language='ru', user=None):
    """Ставит SMS в очередь (в т.ч. OTP — текст кода в БД не сохраняется)."""
    notification = Notification.objects.create(
        user=user,
        channel=NotificationChannel.SMS,
        event=event,
        language=language,
        payload={'phone': phone},
    )

    from apps.notifications.tasks import send_sms_notification
    payload = dict(context or {})

    def _dispatch():
        try:
            send_sms_notification.delay(str(notification.id), phone, event, language, payload)
        except Exception:
            logger.exception('Не удалось поставить SMS %s в очередь', notification.id)

    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(_dispatch)
    else:
        _dispatch()
    return notification


__all__ = ['notify_user', 'send_sms', 'NotificationEvent']

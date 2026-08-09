import logging

from celery import shared_task
from django.utils import timezone

from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.providers.push import PushSendError, get_push_provider
from apps.notifications.providers.sms import SmsSendError, get_sms_provider
from apps.notifications.templates_registry import render_push, render_sms

logger = logging.getLogger('alasoft.notifications')


@shared_task(name='notifications.send_push', bind=True, max_retries=3, default_retry_delay=30)
def send_push_notification(self, notification_id):
    """SendPush: доставка на все активные устройства пользователя."""
    notification = Notification.objects.filter(pk=notification_id).select_related('user').first()
    if notification is None or notification.status != NotificationStatus.QUEUED:
        return 'skipped'

    user = notification.user
    devices = list(user.devices.filter(is_active=True).exclude(fcm_token='')) if user else []
    title, body = render_push(notification.event, notification.language, notification.payload)
    notification.title, notification.body = title, body

    if not devices:
        notification.status = NotificationStatus.SKIPPED
        notification.error = 'Нет активных устройств с FCM-токеном'
        notification.save(update_fields=['title', 'body', 'status', 'error', 'updated_at'])
        return 'no_devices'

    provider = get_push_provider()
    sent, last_error = 0, ''
    for device in devices:
        try:
            message_id = provider.send(
                device.fcm_token, title, body,
                {'event': notification.event, **notification.payload},
            )
            notification.provider_message_id = message_id or ''
            sent += 1
        except PushSendError as exc:
            last_error = str(exc)[:255]
            if exc.invalid_token:
                # Невалидный токен удаляем, чтобы не долбить провайдера (§25).
                device.fcm_token = ''
                device.is_active = False
                device.save(update_fields=['fcm_token', 'is_active', 'updated_at'])
        except Exception as exc:
            last_error = str(exc)[:255]

    notification.provider = provider.name
    notification.status = NotificationStatus.SENT if sent else NotificationStatus.FAILED
    notification.error = '' if sent else last_error
    notification.sent_at = timezone.now() if sent else None
    notification.save(update_fields=[
        'title', 'body', 'provider', 'provider_message_id', 'status', 'error', 'sent_at', 'updated_at'
    ])
    return f'sent:{sent}'


@shared_task(name='notifications.send_sms', bind=True, max_retries=3, default_retry_delay=30)
def send_sms_notification(self, notification_id, phone, event, language, context):
    """SendSms: текст рендерится в задаче, код в БД/логи не попадает."""
    notification = Notification.objects.filter(pk=notification_id).first()
    text = render_sms(event, language, context or {})
    provider = get_sms_provider()

    try:
        message_id = provider.send(phone, text)
    except SmsSendError as exc:
        if notification:
            notification.status = NotificationStatus.FAILED
            notification.provider = provider.name
            notification.error = str(exc)[:255]
            notification.save(update_fields=['status', 'provider', 'error', 'updated_at'])
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('SMS на %s не отправлена', phone)
            return 'failed'

    if notification:
        notification.status = NotificationStatus.SENT
        notification.provider = provider.name
        notification.provider_message_id = message_id or ''
        notification.sent_at = timezone.now()
        notification.save(update_fields=[
            'status', 'provider', 'provider_message_id', 'sent_at', 'updated_at'
        ])
    return 'sent'

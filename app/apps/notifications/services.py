"""Постановка уведомлений в очередь.

Ошибка отправки push/SMS никогда не откатывает бизнес-транзакцию
(ТЗ backend §25): задачи ставятся только после успешного commit.

Каждое событие пишется в ленту (BACKEND_TZ 9.5) с готовыми заголовком и
текстом на языке получателя и адресуется одному приложению: покупателю
(CLIENT) или заведению (BUSINESS) — у человека могут стоять оба.
"""

import logging

from django.db import transaction

from apps.notifications.models import (
    Notification,
    NotificationApp,
    NotificationChannel,
    NotificationEvent,
)

logger = logging.getLogger('alasoft.notifications')

# Тихие события: без заголовка и текста, только data, в ленту не попадают
SILENT_EVENTS = {NotificationEvent.STAFF_ACCESS_CHANGED}

# Срочные: покупатель стоит у кассы — отправляем сразу, не через общую очередь
URGENT_EVENTS = {NotificationEvent.REDEMPTION_REQUESTED}

# Группы переключателей в настройках уведомлений (9.6).
# REDEMPTION_REQUESTED отключить нельзя: без него не оплатить бонусами.
SETTING_GROUPS = {
    'operations': {
        NotificationEvent.VISIT_EARNED, NotificationEvent.CASHBACK_EARNED,
        NotificationEvent.GIFT_EARNED, NotificationEvent.REDEMPTION_CONFIRMED,
        NotificationEvent.REDEMPTION_REJECTED, NotificationEvent.TRANSACTION_REVERSED,
    },
    'expiring': {NotificationEvent.CASHBACK_EXPIRING, NotificationEvent.CASHBACK_EXPIRED},
    'news': {NotificationEvent.ORGANIZATION_NEWS, NotificationEvent.FRIEND_JOINED},
}
DEFAULT_SETTINGS = {'operations': True, 'expiring': True, 'news': False}


def user_settings(user):
    return {**DEFAULT_SETTINGS, **{
        key: bool(value) for key, value in (getattr(user, 'notification_settings', None) or {}).items()
        if key in DEFAULT_SETTINGS
    }}


def push_enabled(user, event, app):
    """Push заведению не отключается; покупателю — по группам настроек."""
    if app != NotificationApp.CLIENT:
        return True
    settings = user_settings(user)
    for group, events in SETTING_GROUPS.items():
        if event in events:
            return settings[group]
    return True


def notify_user(user, event, *, organization=None, context=None, language=None,
                app=NotificationApp.CLIENT):
    """Сохраняет событие в ленту и ставит push в очередь после commit."""
    if user is None:
        return None

    from apps.notifications.templates_registry import render_push

    context = dict(context or {})
    if organization is not None:
        context.setdefault('organization', organization.name)
        context.setdefault('organization_id', str(organization.id))
        context.setdefault('organization_name', organization.name)

    language = language or getattr(user, 'language', 'ru')
    silent = event in SILENT_EVENTS
    title, body = ('', '') if silent else render_push(event, language, context, app)
    notification = Notification.objects.create(
        user=user,
        organization=organization,
        channel=NotificationChannel.PUSH,
        app=app,
        is_silent=silent,
        event=event,
        language=language,
        title=title[:150],
        body=body,
        payload=context,
    )

    if not push_enabled(user, event, app):
        from apps.notifications.models import NotificationStatus
        Notification.objects.filter(pk=notification.pk).update(
            status=NotificationStatus.SKIPPED, error='Отключено в настройках',
        )
        return notification

    def _dispatch():
        from apps.notifications.tasks import send_push_notification
        try:
            if event in URGENT_EVENTS:
                send_push_notification(str(notification.id))
            else:
                send_push_notification.delay(str(notification.id))
        except Exception:
            logger.exception('Не удалось отправить push %s', notification.id)

    transaction.on_commit(_dispatch)
    return notification


def notify_staff(user, event, *, organization=None, context=None, language=None):
    """Событие заведения — только на устройства TOPTO Business."""
    return notify_user(user, event, organization=organization, context=context,
                       language=language, app=NotificationApp.BUSINESS)


def notify_organization_admins(organization, event, *, context=None, exclude_user=None):
    """Всем действующим администраторам заведения."""
    from apps.organizations.models import EmployeePosition, OrganizationAdmin

    memberships = OrganizationAdmin.objects.filter(
        organization=organization, position=EmployeePosition.ADMIN,
        is_active=True, deleted_at__isnull=True,
    ).select_related('user')
    for membership in memberships:
        if exclude_user is not None and membership.user_id == exclude_user.id:
            continue
        notify_staff(membership.user, event, organization=organization, context=context)


def notify_organization_staff(organization, event, *, context=None):
    """Всем сотрудникам заведения (например, заведение заблокировано)."""
    from apps.organizations.models import OrganizationAdmin

    memberships = OrganizationAdmin.objects.filter(
        organization=organization, deleted_at__isnull=True,
    ).select_related('user')
    for membership in memberships:
        notify_staff(membership.user, event, organization=organization, context=context)


def unread_count(user, app):
    return Notification.objects.filter(
        user=user, app=app, channel=NotificationChannel.PUSH, is_silent=False, is_read=False,
    ).count()


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


__all__ = [
    'notify_user', 'notify_staff', 'notify_organization_admins', 'notify_organization_staff',
    'send_sms', 'unread_count', 'NotificationEvent',
]

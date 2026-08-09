from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class NotificationChannel(models.TextChoices):
    PUSH = 'PUSH', 'Push'
    SMS = 'SMS', 'SMS'


class NotificationStatus(models.TextChoices):
    QUEUED = 'QUEUED', 'В очереди'
    SENT = 'SENT', 'Отправлено'
    FAILED = 'FAILED', 'Ошибка'
    SKIPPED = 'SKIPPED', 'Пропущено'


class NotificationEvent(models.TextChoices):
    VISIT_EARNED = 'VISIT_EARNED', 'Начислено посещение'
    CASHBACK_EARNED = 'CASHBACK_EARNED', 'Начислен cashback'
    GIFT_EARNED = 'GIFT_EARNED', 'Получен подарок'
    REDEMPTION_REQUESTED = 'REDEMPTION_REQUESTED', 'Запрос подтверждения списания'
    REDEMPTION_CONFIRMED = 'REDEMPTION_CONFIRMED', 'Списание подтверждено'
    REDEMPTION_REJECTED = 'REDEMPTION_REJECTED', 'Списание отклонено'
    CASHBACK_EXPIRING = 'CASHBACK_EXPIRING', 'Cashback скоро сгорит'
    CASHBACK_EXPIRED = 'CASHBACK_EXPIRED', 'Cashback сгорел'
    TRANSACTION_REVERSED = 'TRANSACTION_REVERSED', 'Операция отменена'
    SECURITY_ALERT = 'SECURITY_ALERT', 'Событие безопасности'
    OTP = 'OTP', 'Код подтверждения'


class Notification(UUIDModel, TimeStampedModel):
    """Журнал отправленных push/SMS (ТЗ backend §9, §25)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notifications', null=True, blank=True,
    )
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.SET_NULL,
        related_name='notifications', null=True, blank=True,
    )
    channel = models.CharField(max_length=8, choices=NotificationChannel.choices)
    event = models.CharField(max_length=32, choices=NotificationEvent.choices, db_index=True)
    language = models.CharField(max_length=8, default='ru')
    title = models.CharField(max_length=150, blank=True, default='')
    body = models.TextField(blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=NotificationStatus.choices,
        default=NotificationStatus.QUEUED, db_index=True,
    )
    provider = models.CharField(max_length=32, blank=True, default='')
    provider_message_id = models.CharField(max_length=128, blank=True, default='')
    error = models.CharField(max_length=255, blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f'{self.channel}:{self.event}:{self.status}'

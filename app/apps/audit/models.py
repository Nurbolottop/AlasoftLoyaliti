from django.conf import settings
from django.db import models

from apps.common.models import ActorType, UUIDModel


class AuditAction(models.TextChoices):
    LOGIN = 'LOGIN', 'Вход'
    LOGIN_FAILED = 'LOGIN_FAILED', 'Неудачный вход'
    LOGOUT = 'LOGOUT', 'Выход'
    PIN_SET = 'PIN_SET', 'Установлен PIN'
    PIN_RESET = 'PIN_RESET', 'Сброшен PIN'
    ORGANIZATION_CREATED = 'ORGANIZATION_CREATED', 'Организация создана'
    ORGANIZATION_UPDATED = 'ORGANIZATION_UPDATED', 'Организация изменена'
    ORGANIZATION_BLOCKED = 'ORGANIZATION_BLOCKED', 'Организация заблокирована'
    ORGANIZATION_UNBLOCKED = 'ORGANIZATION_UNBLOCKED', 'Организация разблокирована'
    PROGRAM_UPDATED = 'PROGRAM_UPDATED', 'Настройки программы изменены'
    ADMIN_CREATED = 'ADMIN_CREATED', 'Создан администратор'
    ADMIN_RESET = 'ADMIN_RESET', 'Сброшен доступ администратора'
    VISIT_EARNED = 'VISIT_EARNED', 'Начислено посещение'
    CASHBACK_EARNED = 'CASHBACK_EARNED', 'Начислен cashback'
    CASHBACK_SPENT = 'CASHBACK_SPENT', 'Списан cashback'
    GIFT_REDEEMED = 'GIFT_REDEEMED', 'Использован подарок'
    REDEMPTION_CREATED = 'REDEMPTION_CREATED', 'Создан запрос на списание'
    REDEMPTION_CONFIRMED = 'REDEMPTION_CONFIRMED', 'Списание подтверждено'
    REDEMPTION_REJECTED = 'REDEMPTION_REJECTED', 'Списание отклонено'
    TRANSACTION_REVERSED = 'TRANSACTION_REVERSED', 'Транзакция отменена'
    CASHBACK_EXPIRED = 'CASHBACK_EXPIRED', 'Cashback сгорел'


class AuditLog(UUIDModel):
    """Кто, что, над чем и с каким изменением состояния (ТЗ backend §27)."""

    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_id = models.UUIDField(null=True, blank=True)
    actor_phone = models.CharField(max_length=20, blank=True, default='')
    action = models.CharField(max_length=48, choices=AuditAction.choices, db_index=True)
    entity_type = models.CharField(max_length=64, blank=True, default='')
    entity_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_logs',
    )
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        verbose_name = 'Запись аудита'
        verbose_name_plural = 'Аудит'
        indexes = [
            models.Index(fields=['actor_type', 'actor_id', '-created_at']),
            models.Index(fields=['organization', '-created_at']),
        ]

    def __str__(self):
        return f'{self.action} by {self.actor_type}:{self.actor_id}'

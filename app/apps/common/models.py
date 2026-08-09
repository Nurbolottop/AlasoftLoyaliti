import uuid

from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActorType(models.TextChoices):
    USER = 'USER', 'Пользователь'
    ADMIN = 'ADMIN', 'Администратор организации'
    DIRECTOR = 'DIRECTOR', 'Директор AlaSoft'
    SYSTEM = 'SYSTEM', 'Система'


class IdempotencyRecord(UUIDModel, TimeStampedModel):
    """Хранит результат критического POST по ключу Idempotency-Key.

    Повтор с тем же ключом и тем же телом возвращает сохранённый ответ,
    с другим телом — IDEMPOTENCY_CONFLICT (ТЗ backend §23).
    """

    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_id = models.UUIDField(null=True, blank=True)
    key = models.CharField(max_length=255)
    endpoint = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=200)
    response_body = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'idempotency_records'
        constraints = [
            models.UniqueConstraint(
                fields=['actor_type', 'actor_id', 'key'],
                name='uniq_idempotency_actor_key',
            )
        ]
        indexes = [models.Index(fields=['created_at'])]

    def __str__(self):
        return f'{self.actor_type}:{self.key}'

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import ActorType, TimeStampedModel, UUIDModel
from apps.organizations.models import Organization


class TransactionType(models.TextChoices):
    VISIT_EARN = 'VISIT_EARN', 'Начислено посещение'
    GIFT_CREATED = 'GIFT_CREATED', 'Получен подарок'
    GIFT_REDEEM = 'GIFT_REDEEM', 'Подарок использован'
    CASHBACK_EARN = 'CASHBACK_EARN', 'Начислен cashback'
    CASHBACK_SPEND = 'CASHBACK_SPEND', 'Списан cashback'
    CASHBACK_EXPIRE = 'CASHBACK_EXPIRE', 'Cashback сгорел'
    REVERSAL = 'REVERSAL', 'Операция отменена'


class TransactionStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', 'Выполнена'
    REVERSED = 'REVERSED', 'Отменена'
    PENDING = 'PENDING', 'Ожидает'
    FAILED = 'FAILED', 'Ошибка'


class UserOrganizationState(UUIDModel, TimeStampedModel):
    """Агрегат состояния клиента в организации.

    Ускоряет чтение, но историческим источником истины остаются transactions
    и cashback_lots. Меняется только внутри транзакции с SELECT FOR UPDATE
    (ТЗ backend §10).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='loyalty_states'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='user_states'
    )

    visit_progress = models.PositiveIntegerField(default=0)
    total_visits = models.PositiveIntegerField(default=0)
    available_gifts = models.PositiveIntegerField(default=0)
    total_gifts_earned = models.PositiveIntegerField(default=0)
    total_gifts_used = models.PositiveIntegerField(default=0)

    cashback_available = models.BigIntegerField(default=0)
    cashback_total_earned = models.BigIntegerField(default=0)
    cashback_total_spent = models.BigIntegerField(default=0)
    cashback_total_expired = models.BigIntegerField(default=0)

    first_activity_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'user_organization_states'
        verbose_name = 'Состояние клиента в организации'
        verbose_name_plural = 'Состояния клиентов в организациях'
        constraints = [
            models.UniqueConstraint(fields=['user', 'organization'], name='uniq_user_org_state')
        ]
        indexes = [
            models.Index(fields=['organization', 'last_activity_at']),
            models.Index(fields=['user', 'last_activity_at']),
        ]

    def __str__(self):
        return f'{self.user_id}@{self.organization_id}'

    def touch(self):
        now = timezone.now()
        if self.first_activity_at is None:
            self.first_activity_at = now
        self.last_activity_at = now


class Transaction(UUIDModel):
    """Неизменяемый ledger. Записи не удаляются и не правятся (ТЗ backend §11)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='transactions'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name='transactions'
    )
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_id = models.UUIDField(null=True, blank=True)

    type = models.CharField(max_length=24, choices=TransactionType.choices, db_index=True)
    amount = models.BigIntegerField(null=True, blank=True, help_text='Тыйын; NULL для VISIT/GIFT')
    status = models.CharField(
        max_length=16, choices=TransactionStatus.choices,
        default=TransactionStatus.COMPLETED, db_index=True,
    )
    related_transaction = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, related_name='related_transactions'
    )
    idempotency_key = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'transactions'
        ordering = ['-created_at', '-id']
        verbose_name = 'Транзакция'
        verbose_name_plural = 'Транзакции'
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['organization', '-created_at']),
            models.Index(fields=['user', 'organization', '-created_at']),
            models.Index(fields=['type', '-created_at']),
        ]

    def __str__(self):
        return f'{self.type}:{self.id}'

    @property
    def is_reversed(self):
        return self.status == TransactionStatus.REVERSED


class GiftStatus(models.TextChoices):
    AVAILABLE = 'AVAILABLE', 'Доступен'
    PENDING_REDEMPTION = 'PENDING_REDEMPTION', 'Ожидает подтверждения'
    USED = 'USED', 'Использован'
    CANCELLED = 'CANCELLED', 'Аннулирован'


class Gift(UUIDModel, TimeStampedModel):
    """Подарок как отдельная сущность: их может накопиться несколько."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='gifts'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name='gifts'
    )
    status = models.CharField(
        max_length=24, choices=GiftStatus.choices, default=GiftStatus.AVAILABLE, db_index=True
    )
    title_ru = models.CharField(max_length=150, blank=True, default='Подарок')
    title_ky = models.CharField(max_length=150, blank=True, default='Белек')
    source_transaction = models.ForeignKey(
        Transaction, on_delete=models.PROTECT, null=True, blank=True, related_name='created_gifts'
    )
    redeem_transaction = models.ForeignKey(
        Transaction, on_delete=models.PROTECT, null=True, blank=True, related_name='redeemed_gifts'
    )
    used_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'gifts'
        ordering = ['-created_at']
        verbose_name = 'Подарок'
        verbose_name_plural = 'Подарки'
        indexes = [
            models.Index(fields=['user', 'organization', 'status']),
            models.Index(fields=['organization', 'status']),
        ]

    def __str__(self):
        return f'Gift {self.id} ({self.status})'

    def title(self, language='ru'):
        return self.title_ky if language == 'ky' else self.title_ru


class CashbackLotStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Активен'
    SPENT = 'SPENT', 'Израсходован'
    EXPIRED = 'EXPIRED', 'Сгорел'
    CANCELLED = 'CANCELLED', 'Аннулирован'


class CashbackLot(UUIDModel, TimeStampedModel):
    """Отдельное начисление со своим сроком сгорания. Списание — FIFO."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cashback_lots'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name='cashback_lots'
    )
    original_amount = models.BigIntegerField()
    remaining_amount = models.BigIntegerField()
    status = models.CharField(
        max_length=16, choices=CashbackLotStatus.choices,
        default=CashbackLotStatus.ACTIVE, db_index=True,
    )
    earned_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    source_transaction = models.ForeignKey(
        Transaction, on_delete=models.PROTECT, null=True, blank=True, related_name='cashback_lots'
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'cashback_lots'
        ordering = ['expires_at', 'earned_at']
        verbose_name = 'Начисление cashback'
        verbose_name_plural = 'Начисления cashback'
        indexes = [
            models.Index(
                fields=['user', 'organization', 'expires_at', 'remaining_amount'],
                name='idx_lot_fifo',
            ),
            models.Index(fields=['status', 'expires_at'], name='idx_lot_expiry_scan'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(remaining_amount__gte=0),
                name='cashback_lot_remaining_non_negative',
            ),
        ]

    def __str__(self):
        return f'Lot {self.id}: {self.remaining_amount}/{self.original_amount}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class RedemptionType(models.TextChoices):
    GIFT = 'GIFT', 'Подарок'
    CASHBACK = 'CASHBACK', 'Cashback'


class RedemptionStatus(models.TextChoices):
    PENDING = 'PENDING', 'Ожидает подтверждения'
    CONFIRMED = 'CONFIRMED', 'Подтверждено'
    REJECTED = 'REJECTED', 'Отклонено'
    EXPIRED = 'EXPIRED', 'Истекло'
    CANCELLED = 'CANCELLED', 'Отменено'


class RedemptionRequest(UUIDModel, TimeStampedModel):
    """Подтверждение списания пользователем (ТЗ общее §12, backend §16)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='redemption_requests'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name='redemption_requests'
    )
    type = models.CharField(max_length=16, choices=RedemptionType.choices)
    gift = models.ForeignKey(
        Gift, on_delete=models.PROTECT, null=True, blank=True, related_name='redemption_requests'
    )

    purchase_total = models.BigIntegerField(null=True, blank=True)
    spend_amount = models.BigIntegerField(null=True, blank=True)
    cash_paid = models.BigIntegerField(null=True, blank=True)
    earn_amount = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=16, choices=RedemptionStatus.choices,
        default=RedemptionStatus.PENDING, db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    created_by_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='created_redemptions', null=True, blank=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=255, blank=True, default='')
    idempotency_key = models.CharField(max_length=255, blank=True, default='')
    result_transactions = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'redemption_requests'
        ordering = ['-created_at']
        verbose_name = 'Запрос на списание'
        verbose_name_plural = 'Запросы на списание'
        indexes = [
            models.Index(fields=['user', 'status', '-created_at']),
            models.Index(fields=['organization', 'status', '-created_at']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f'{self.type} {self.id} ({self.status})'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

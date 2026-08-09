from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django_resized import ResizedImageField

from apps.common.models import TimeStampedModel, UUIDModel
from apps.organizations.validators import validate_logo_size


class LoyaltyType(models.TextChoices):
    VISIT = 'VISIT', 'Накопительная (N+1)'
    CASHBACK = 'CASHBACK', 'Cashback'


class OrganizationStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Активна'
    BLOCKED = 'BLOCKED', 'Заблокирована'


class Category(UUIDModel, TimeStampedModel):
    slug = models.SlugField(max_length=64, unique=True)
    name_ru = models.CharField(max_length=100)
    name_ky = models.CharField(max_length=100)
    icon = models.CharField(max_length=64, blank=True, default='')
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'categories'
        ordering = ['sort_order', 'name_ru']
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'

    def __str__(self):
        return self.name_ru

    def name(self, language='ru'):
        return self.name_ky if language == 'ky' else self.name_ru


class Organization(UUIDModel, TimeStampedModel):
    """Tenant платформы. Данные лояльности изолированы по organization_id."""

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True)
    logo = ResizedImageField(
        upload_to='organizations/logos/',
        null=True,
        blank=True,
        force_format='WEBP',
        quality=85,
        size=[512, 512],
        validators=[
            FileExtensionValidator(allowed_extensions=settings.LOGO_ALLOWED_EXTENSIONS),
            validate_logo_size,
        ],
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='organizations', null=True, blank=True
    )
    description_ru = models.TextField(blank=True, default='')
    description_ky = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    address = models.CharField(max_length=255, blank=True, default='')
    # [{"day": 1, "open": "09:00", "close": "21:00", "is_closed": false}, ...]
    working_hours = models.JSONField(default=list, blank=True)

    loyalty_type = models.CharField(max_length=16, choices=LoyaltyType.choices)
    status = models.CharField(
        max_length=16, choices=OrganizationStatus.choices,
        default=OrganizationStatus.ACTIVE, db_index=True,
    )
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_reason = models.CharField(max_length=255, blank=True, default='')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='created_organizations', null=True, blank=True,
    )

    class Meta:
        db_table = 'organizations'
        ordering = ['name']
        verbose_name = 'Организация'
        verbose_name_plural = 'Организации'
        indexes = [
            models.Index(fields=['status', 'loyalty_type']),
            models.Index(fields=['category', 'status']),
        ]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == OrganizationStatus.ACTIVE

    @property
    def program(self):
        """Активная программа лояльности организации."""
        if self.loyalty_type == LoyaltyType.VISIT:
            return getattr(self, 'visit_program', None)
        return getattr(self, 'cashback_program', None)

    def description(self, language='ru'):
        if language == 'ky' and self.description_ky:
            return self.description_ky
        return self.description_ru


class VisitProgram(UUIDModel, TimeStampedModel):
    """Условие N+1: target_visits посещений дают reward_count подарков."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name='visit_program'
    )
    target_visits = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    reward_count = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    reward_title_ru = models.CharField(max_length=150, blank=True, default='Подарок')
    reward_title_ky = models.CharField(max_length=150, blank=True, default='Белек')
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'visit_programs'
        verbose_name = 'Программа VISIT'
        verbose_name_plural = 'Программы VISIT'

    def __str__(self):
        return f'{self.organization.name}: {self.target_visits}+{self.reward_count}'

    def reward_title(self, language='ru'):
        return self.reward_title_ky if language == 'ky' else self.reward_title_ru


class CashbackProgram(UUIDModel, TimeStampedModel):
    """Ставка начисления, лимит списания и срок сгорания — в bps и днях."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name='cashback_program'
    )
    cashback_rate_bps = models.PositiveIntegerField(
        default=500, validators=[MinValueValidator(1), MaxValueValidator(10_000)]
    )
    max_spend_percent_bps = models.PositiveIntegerField(
        default=3_000, validators=[MinValueValidator(1), MaxValueValidator(10_000)]
    )
    expiry_days = models.PositiveIntegerField(
        default=90, validators=[MinValueValidator(1), MaxValueValidator(3_650)]
    )
    min_purchase_amount = models.BigIntegerField(default=0, help_text='В тыйынах')
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'cashback_programs'
        verbose_name = 'Программа CASHBACK'
        verbose_name_plural = 'Программы CASHBACK'

    def __str__(self):
        return f'{self.organization.name}: {self.cashback_rate_bps / 100}%'

    def snapshot(self):
        """Параметры фиксируются в metadata транзакции (ТЗ backend §8)."""
        return {
            'cashback_rate_bps': self.cashback_rate_bps,
            'max_spend_percent_bps': self.max_spend_percent_bps,
            'expiry_days': self.expiry_days,
        }


class OrganizationAdmin(UUIDModel, TimeStampedModel):
    """Привязка администратора к организации. В MVP — один активный."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='admins'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='admin_memberships'
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='created_org_admins', null=True, blank=True,
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'organization_admins'
        verbose_name = 'Администратор организации'
        verbose_name_plural = 'Администраторы организаций'
        constraints = [
            models.UniqueConstraint(
                fields=['organization'],
                condition=models.Q(is_active=True),
                name='uniq_active_admin_per_org',
            ),
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(is_active=True),
                name='uniq_active_org_per_admin',
            ),
        ]

    def __str__(self):
        return f'{self.organization.name} ← {self.user.phone}'

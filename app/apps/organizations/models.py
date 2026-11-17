from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django_resized import ResizedImageField

from apps.common.models import TimeStampedModel, UUIDModel
from apps.organizations.validators import validate_image_size, validate_logo_size


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
    # day: 1 — понедельник … 7 — воскресенье; close < open — закрытие после полуночи.
    working_hours = models.JSONField(default=list, blank=True)

    # Витрина заведения у покупателя (TOPTO)
    cover = ResizedImageField(
        upload_to='organizations/covers/',
        null=True, blank=True,
        force_format='WEBP', quality=82, size=[1600, 1600], keep_meta=False,
        validators=[
            FileExtensionValidator(allowed_extensions=settings.LOGO_ALLOWED_EXTENSIONS),
            validate_image_size,
        ],
    )
    kind = models.CharField(max_length=60, blank=True, default='', help_text='«Барбершоп», «Кофейня»')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    instagram = models.CharField(max_length=255, blank=True, default='', help_text='@ник или ссылка')
    website = models.CharField(max_length=255, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    card_title = models.CharField(max_length=30, blank=True, default='')
    card_description = models.CharField(max_length=100, blank=True, default='')
    showcase_title = models.CharField(max_length=60, blank=True, default='')

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

    @property
    def instagram_url(self):
        value = (self.instagram or '').strip()
        if not value:
            return ''
        if value.startswith(('http://', 'https://')):
            return value
        return f'https://instagram.com/{value.lstrip("@").strip("/")}'


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
    # Макет этот лимит не настраивает: по умолчанию бонусами можно оплатить всю покупку.
    max_spend_percent_bps = models.PositiveIntegerField(
        default=10_000, validators=[MinValueValidator(1), MaxValueValidator(10_000)]
    )
    # 0 — кешбэк не сгорает («Бессрочно»)
    expiry_days = models.PositiveIntegerField(
        default=90, validators=[MinValueValidator(0), MaxValueValidator(3_650)]
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


class EmployeePosition(models.TextChoices):
    ADMIN = 'ADMIN', 'Администратор'
    MASTER = 'MASTER', 'Мастер'
    CASHIER = 'CASHIER', 'Кассир'
    INTERN = 'INTERN', 'Стажёр'


EMPLOYEE_PERMISSIONS = ('can_scan', 'can_accept_payment', 'can_award_bonuses')


class OrganizationAdmin(UUIDModel, TimeStampedModel):
    """Сотрудник заведения (TOPTO Business, раздел 5).

    Исторически здесь была только привязка администратора, поэтому имя модели
    и таблицы прежние. Должность ADMIN даёт роль ORGANIZATION_ADMIN, остальные —
    ORGANIZATION_EMPLOYEE. Один человек работает в одном заведении; удаление
    мягкое: операции удалённого сотрудника остаются в истории с его именем.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='admins'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='admin_memberships'
    )
    full_name = models.CharField(max_length=150, blank=True, default='')
    position = models.CharField(
        max_length=16, choices=EmployeePosition.choices, default=EmployeePosition.ADMIN,
    )
    can_accept_payment = models.BooleanField(default=True)
    can_award_bonuses = models.BooleanField(default=True)
    can_scan = models.BooleanField(default=True)
    avatar = ResizedImageField(
        upload_to='organizations/employees/',
        null=True, blank=True,
        force_format='WEBP', quality=85, size=[512, 512], keep_meta=False,
        validators=[
            FileExtensionValidator(allowed_extensions=settings.AVATAR_ALLOWED_EXTENSIONS),
            validate_image_size,
        ],
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='created_org_admins', null=True, blank=True,
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'organization_admins'
        verbose_name = 'Сотрудник организации'
        verbose_name_plural = 'Сотрудники организаций'
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(deleted_at__isnull=True),
                name='uniq_live_membership_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.organization.name} ← {self.user.phone}'

    @property
    def is_admin(self):
        return self.position == EmployeePosition.ADMIN

    @property
    def display_name(self):
        return self.full_name or self.user.full_name or self.user.phone

    def has_permission(self, permission):
        """У администратора все права включены всегда (5.1)."""
        if self.is_admin:
            return True
        return bool(getattr(self, permission, False))


Employee = OrganizationAdmin


class CatalogSection(UUIDModel, TimeStampedModel):
    """Раздел витрины заведения: «Кофе», «Завтраки», «Стрижки»."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='catalog_sections'
    )
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'catalog_sections'
        ordering = ['sort_order', 'created_at']
        verbose_name = 'Раздел каталога'
        verbose_name_plural = 'Разделы каталога'

    def __str__(self):
        return f'{self.organization.name}: {self.name}'


class CatalogItem(UUIDModel, TimeStampedModel):
    """Товар или услуга. Название и цена копируются в операцию (3.4)."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='catalog_items'
    )
    section = models.ForeignKey(CatalogSection, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=80)
    price = models.BigIntegerField(validators=[MinValueValidator(1)], help_text='Тыйын')
    image = ResizedImageField(
        upload_to='organizations/catalog/',
        null=True, blank=True,
        force_format='WEBP', quality=85, size=[1200, 1200], keep_meta=False,
        validators=[
            FileExtensionValidator(allowed_extensions=settings.LOGO_ALLOWED_EXTENSIONS),
            validate_image_size,
        ],
    )
    short_description = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True, default='')
    composition = models.CharField(max_length=200, blank=True, default='')
    volume = models.CharField(max_length=100, blank=True, default='')
    duration = models.CharField(max_length=100, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'catalog_items'
        ordering = ['sort_order', 'created_at']
        verbose_name = 'Позиция каталога'
        verbose_name_plural = 'Позиции каталога'
        indexes = [models.Index(fields=['organization', 'section'])]

    def __str__(self):
        return self.name


class FavoriteOrganization(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favorite_organizations'
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='favorited_by'
    )

    class Meta:
        db_table = 'favorite_organizations'
        constraints = [
            models.UniqueConstraint(fields=['user', 'organization'], name='uniq_favorite_org'),
        ]


class FavoriteItem(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favorite_items'
    )
    item = models.ForeignKey(CatalogItem, on_delete=models.CASCADE, related_name='favorited_by')

    class Meta:
        db_table = 'favorite_items'
        constraints = [
            models.UniqueConstraint(fields=['user', 'item'], name='uniq_favorite_item'),
        ]

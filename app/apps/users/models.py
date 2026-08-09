import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel
from apps.users.managers import UserManager

PUBLIC_CODE_LENGTH = 6
PUBLIC_CODE_MAX_TRIES = 50


class Role(models.TextChoices):
    USER = 'USER', 'Пользователь'
    ORGANIZATION_ADMIN = 'ORGANIZATION_ADMIN', 'Администратор организации'
    DIRECTOR = 'DIRECTOR', 'Директор AlaSoft'


class Language(models.TextChoices):
    RU = 'ru', 'Русский'
    KY = 'ky', 'Кыргызча'


def generate_public_code() -> str:
    """Глобально уникальный 6-значный код (ТЗ backend §4)."""
    for _ in range(PUBLIC_CODE_MAX_TRIES):
        code = f'{secrets.randbelow(900_000) + 100_000}'
        if not User.objects.filter(public_code=code).exists():
            return code
    raise RuntimeError('Не удалось подобрать свободный public_code')


def generate_qr_token() -> str:
    """Opaque-токен QR: не несёт ФИО/телефон/баланс (ТЗ общее §7)."""
    return secrets.token_urlsafe(32)


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):
    """Единая учётка платформы. Роль определяет доступный API-скоуп."""

    phone = models.CharField(max_length=20, unique=True, db_index=True)
    email = models.EmailField(blank=True, default='')
    first_name = models.CharField(max_length=100, blank=True, default='')
    last_name = models.CharField(max_length=100, blank=True, default='')
    birth_date = models.DateField(null=True, blank=True)

    role = models.CharField(max_length=32, choices=Role.choices, default=Role.USER, db_index=True)
    language = models.CharField(max_length=8, choices=Language.choices, default=Language.RU)

    # Идентификаторы клиента: генерируются только для роли USER.
    public_code = models.CharField(max_length=PUBLIC_CODE_LENGTH, unique=True, null=True, blank=True, db_index=True)
    qr_token = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)

    # PIN хранится отдельно от password и только в виде Argon2id-хеша.
    pin_hash = models.CharField(max_length=255, blank=True, default='')
    pin_updated_at = models.DateTimeField(null=True, blank=True)
    failed_pin_attempts = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_registration_complete = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'
        indexes = [
            models.Index(fields=['role', 'is_active']),
            models.Index(fields=['date_joined']),
        ]

    def __str__(self):
        return f'{self.phone} ({self.role})'

    @property
    def full_name(self):
        return ' '.join(part for part in [self.first_name, self.last_name] if part).strip()

    @property
    def has_pin(self):
        return bool(self.pin_hash)

    def ensure_identity(self):
        """Выдаёт public_code и qr_token клиенту (идемпотентно)."""
        changed = []
        if self.role == Role.USER:
            if not self.public_code:
                self.public_code = generate_public_code()
                changed.append('public_code')
            if not self.qr_token:
                self.qr_token = generate_qr_token()
                changed.append('qr_token')
        if changed and self.pk:
            self.save(update_fields=changed)
        return changed

    def rotate_qr_token(self):
        self.qr_token = generate_qr_token()
        self.save(update_fields=['qr_token'])
        return self.qr_token

    def set_pin(self, raw_pin: str):
        self.pin_hash = make_password(str(raw_pin))
        self.pin_updated_at = timezone.now()
        self.failed_pin_attempts = 0
        self.pin_locked_until = None

    def check_pin(self, raw_pin: str) -> bool:
        if not self.pin_hash:
            return False

        def setter(new_hash):
            User.objects.filter(pk=self.pk).update(pin_hash=new_hash)

        return check_password(str(raw_pin), self.pin_hash, setter)

    @property
    def is_pin_locked(self):
        return bool(self.pin_locked_until and self.pin_locked_until > timezone.now())

    def register_failed_pin(self):
        self.failed_pin_attempts += 1
        fields = ['failed_pin_attempts']
        if self.failed_pin_attempts >= settings.PIN_MAX_ATTEMPTS:
            self.pin_locked_until = timezone.now() + timedelta(seconds=settings.PIN_LOCKOUT_SECONDS)
            self.failed_pin_attempts = 0
            fields.append('pin_locked_until')
        self.save(update_fields=fields)

    def reset_pin_attempts(self):
        if self.failed_pin_attempts or self.pin_locked_until:
            self.failed_pin_attempts = 0
            self.pin_locked_until = None
            self.save(update_fields=['failed_pin_attempts', 'pin_locked_until'])

    @property
    def active_admin_membership(self):
        """Активная привязка к организации для роли ORGANIZATION_ADMIN."""
        if self.role != Role.ORGANIZATION_ADMIN:
            return None
        cached = getattr(self, '_active_admin_membership', None)
        if cached is not None:
            return cached
        membership = (
            self.admin_memberships.select_related('organization')
            .filter(is_active=True)
            .first()
        )
        self._active_admin_membership = membership
        return membership


class UserDevice(UUIDModel, TimeStampedModel):
    """Сессия устройства: FCM-токен + активный refresh (ТЗ backend §5, §25)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='devices')
    device_id = models.CharField(max_length=128)
    platform = models.CharField(
        max_length=16,
        choices=[('ios', 'iOS'), ('android', 'Android'), ('web', 'Web')],
        default='android',
    )
    device_name = models.CharField(max_length=128, blank=True, default='')
    app_version = models.CharField(max_length=32, blank=True, default='')
    fcm_token = models.CharField(max_length=512, blank=True, default='')
    language = models.CharField(max_length=8, choices=Language.choices, default=Language.RU)
    refresh_jti = models.CharField(max_length=64, blank=True, default='', db_index=True)
    is_trusted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'user_devices'
        constraints = [
            models.UniqueConstraint(fields=['user', 'device_id'], name='uniq_user_device')
        ]
        indexes = [models.Index(fields=['user', 'is_active'])]

    def __str__(self):
        return f'{self.user_id}:{self.device_id}'


class OtpPurpose(models.TextChoices):
    REGISTER = 'REGISTER', 'Регистрация'
    LOGIN = 'LOGIN', 'Вход'
    PIN_RESET = 'PIN_RESET', 'Сброс PIN'


class OtpStatus(models.TextChoices):
    PENDING = 'PENDING', 'Ожидает подтверждения'
    VERIFIED = 'VERIFIED', 'Подтверждён'
    CONSUMED = 'CONSUMED', 'Использован'
    EXPIRED = 'EXPIRED', 'Истёк'
    FAILED = 'FAILED', 'Исчерпаны попытки'


class OtpChallenge(UUIDModel, TimeStampedModel):
    """OTP-челлендж. Код хранится только в виде хеша (ТЗ backend §5)."""

    phone = models.CharField(max_length=20, db_index=True)
    purpose = models.CharField(max_length=16, choices=OtpPurpose.choices)
    code_hash = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=OtpStatus.choices, default=OtpStatus.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    expires_at = models.DateTimeField(db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    verification_token = models.CharField(max_length=64, blank=True, default='', db_index=True)
    verification_expires_at = models.DateTimeField(null=True, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    device_id = models.CharField(max_length=128, blank=True, default='')

    class Meta:
        db_table = 'otp_challenges'
        indexes = [
            models.Index(fields=['phone', 'purpose', 'status']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f'{self.phone}:{self.purpose}:{self.status}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def set_code(self, raw_code: str):
        self.code_hash = make_password(str(raw_code))

    def check_code(self, raw_code: str) -> bool:
        return check_password(str(raw_code), self.code_hash)

    def issue_verification_token(self):
        self.verification_token = secrets.token_urlsafe(32)
        self.verification_expires_at = timezone.now() + timedelta(
            seconds=settings.OTP_VERIFICATION_TTL_SECONDS
        )
        return self.verification_token

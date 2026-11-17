from django.conf import settings
from rest_framework import serializers

from apps.common.phone import normalize_phone
from apps.users.models import AppKind, Gender, Language, OtpPurpose, User, UserDevice


def avatar_url(user, request=None) -> str | None:
    if not getattr(user, 'avatar', None):
        return None
    return request.build_absolute_uri(user.avatar.url) if request else user.avatar.url


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        return normalize_phone(super().to_internal_value(data))


class PinField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault('write_only', True)
        kwargs.setdefault('trim_whitespace', False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = str(super().to_internal_value(data))
        if not value.isdigit() or len(value) != settings.PIN_LENGTH:
            raise serializers.ValidationError(f'PIN должен состоять из {settings.PIN_LENGTH} цифр')
        return value


class DeviceSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128)
    platform = serializers.ChoiceField(choices=['ios', 'android', 'web'], default='android')
    device_name = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    fcm_token = serializers.CharField(max_length=512, required=False, allow_blank=True, default='')
    language = serializers.ChoiceField(choices=Language.choices, required=False, default=Language.RU)
    # CLIENT / BUSINESS. Старые версии не шлют — берётся из X-App или роли.
    app = serializers.ChoiceField(choices=AppKind.choices, required=False)


class OtpRequestSerializer(serializers.Serializer):
    phone = PhoneField()
    purpose = serializers.ChoiceField(choices=OtpPurpose.choices, default=OtpPurpose.REGISTER)
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')


class OtpVerifySerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    phone = PhoneField()
    code = serializers.CharField(max_length=8, write_only=True)


class RegisterCompleteSerializer(serializers.Serializer):
    phone = PhoneField()
    verification_token = serializers.CharField(max_length=64, write_only=True)
    # PIN хранится на устройстве — серверу он необязателен (BACKEND_TZ 4.1)
    pin = PinField(required=False, allow_blank=True)
    referral_code = serializers.CharField(max_length=16, required=False, allow_blank=True, default='')
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    language = serializers.ChoiceField(choices=Language.choices, required=False, default=Language.RU)
    device = DeviceSerializer(required=False)


class OtpLoginSerializer(serializers.Serializer):
    """Вход по подтверждённому SMS-коду, без изменения PIN."""

    phone = PhoneField()
    verification_token = serializers.CharField(max_length=64, write_only=True)
    device = DeviceSerializer(required=False)


class PinLoginSerializer(serializers.Serializer):
    phone = PhoneField()
    pin = PinField()
    device = DeviceSerializer(required=False)


class PinResetSerializer(serializers.Serializer):
    phone = PhoneField()
    verification_token = serializers.CharField(max_length=64, write_only=True)
    pin = PinField()
    device = DeviceSerializer(required=False)


class DirectorLoginSerializer(serializers.Serializer):
    phone = PhoneField()
    password = serializers.CharField(write_only=True)


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')


class MeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    has_pin = serializers.BooleanField(read_only=True)
    qr_payload = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'phone', 'first_name', 'last_name', 'full_name', 'birth_date', 'gender',
            'avatar_url', 'role', 'language', 'public_code', 'qr_payload', 'has_pin',
            'is_registration_complete', 'date_joined',
        ]
        read_only_fields = ['id', 'phone', 'public_code', 'is_registration_complete', 'date_joined']

    def get_role(self, obj) -> str:
        """Роль зависит от X-App: в TOPTO — USER, в Business — по записи сотрудника (5.2)."""
        from apps.common.permissions import effective_role
        return effective_role(obj, self.context.get('request'))

    def get_qr_payload(self, obj) -> str | None:
        """QR несёт только opaque-токен: ни ФИО, ни телефона, ни баланса."""
        return f'alasoft://u/{obj.qr_token}' if obj.qr_token else None

    def get_avatar_url(self, obj) -> str | None:
        return avatar_url(obj, self.context.get('request'))


class MeUpdateSerializer(serializers.ModelSerializer):
    gender = serializers.ChoiceField(choices=Gender.choices, required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'birth_date', 'language', 'gender']

    def validate_gender(self, value):
        return value or None


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = [
            'id', 'device_id', 'platform', 'device_name', 'app_version',
            'fcm_token', 'language', 'app', 'is_active', 'last_seen_at', 'created_at',
        ]
        read_only_fields = ['id', 'is_active', 'last_seen_at', 'created_at']
        extra_kwargs = {
            'fcm_token': {'write_only': True, 'required': False},
            'app': {'required': False},
        }
        # Уникальность (user, device_id, app) обеспечивает upsert, а не валидатор
        validators = []


class UserShortSerializer(serializers.ModelSerializer):
    """Минимум PII для админского экрана."""

    full_name = serializers.CharField(read_only=True)
    phone_masked = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'first_name', 'public_code', 'phone_masked', 'avatar_url']

    def get_phone_masked(self, obj) -> str:
        from apps.common.phone import mask_phone
        return mask_phone(obj.phone)

    def get_avatar_url(self, obj) -> str | None:
        return avatar_url(obj, self.context.get('request'))


class PhoneChangeRequestSerializer(serializers.Serializer):
    """Шаг 1 смены номера: код уходит на новый телефон."""

    new_phone = PhoneField()


class PhoneChangeConfirmSerializer(serializers.Serializer):
    """Шаг 2: номер меняется только после подтверждения кода."""

    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=8, write_only=True)


class AvatarUploadSerializer(serializers.Serializer):
    file = serializers.ImageField(write_only=True)


class AvatarResponseSerializer(serializers.Serializer):
    avatar_url = serializers.CharField(allow_null=True)


class AccountDeleteConfirmSerializer(serializers.Serializer):
    """Подтверждение удаления аккаунта кодом с текущего номера."""

    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=8, write_only=True)

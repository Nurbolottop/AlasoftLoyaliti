from django.conf import settings
from rest_framework import serializers

from apps.common.phone import normalize_phone
from apps.users.models import Language, OtpPurpose, User, UserDevice


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
    pin = PinField()
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    language = serializers.ChoiceField(choices=Language.choices, required=False, default=Language.RU)
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

    class Meta:
        model = User
        fields = [
            'id', 'phone', 'first_name', 'last_name', 'full_name', 'birth_date',
            'role', 'language', 'public_code', 'qr_payload', 'has_pin',
            'is_registration_complete', 'date_joined',
        ]
        read_only_fields = ['id', 'phone', 'role', 'public_code', 'is_registration_complete', 'date_joined']

    def get_qr_payload(self, obj) -> str | None:
        """QR несёт только opaque-токен: ни ФИО, ни телефона, ни баланса."""
        return f'alasoft://u/{obj.qr_token}' if obj.qr_token else None


class MeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'birth_date', 'language']


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = [
            'id', 'device_id', 'platform', 'device_name', 'app_version',
            'fcm_token', 'language', 'is_active', 'last_seen_at', 'created_at',
        ]
        read_only_fields = ['id', 'is_active', 'last_seen_at', 'created_at']
        extra_kwargs = {'fcm_token': {'write_only': True}}


class UserShortSerializer(serializers.ModelSerializer):
    """Минимум PII для админского экрана."""

    full_name = serializers.CharField(read_only=True)
    phone_masked = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'first_name', 'public_code', 'phone_masked']

    def get_phone_masked(self, obj) -> str:
        from apps.common.phone import mask_phone
        return mask_phone(obj.phone)

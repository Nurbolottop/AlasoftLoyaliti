from rest_framework import serializers

from apps.audit.models import AuditLog
from apps.common.phone import normalize_phone
from apps.loyalty.serializers import TransactionSerializer
from apps.organizations.serializers import (
    CashbackProgramSerializer,
    OrganizationAdminWriteSerializer,
    VisitProgramSerializer,
)
from apps.users.models import User


class OrganizationCreateSerializer(OrganizationAdminWriteSerializer):
    slug = serializers.SlugField(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get('loyalty_type'):
            raise serializers.ValidationError({'loyalty_type': 'Обязательное поле'})
        return attrs


class OrganizationUpdateSerializer(OrganizationAdminWriteSerializer):
    loyalty_type = serializers.CharField(required=False)


class BlockSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class CreateOrganizationAdminSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    pin = serializers.CharField(max_length=8, required=False, allow_blank=True)
    replace_existing = serializers.BooleanField(required=False, default=False)

    def validate_phone(self, value):
        return normalize_phone(value)

    def validate_pin(self, value):
        if value and (not value.isdigit() or len(value) != 4):
            raise serializers.ValidationError('PIN должен состоять из 4 цифр')
        return value


class DirectorUserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'phone', 'first_name', 'last_name', 'full_name', 'public_code',
            'language', 'is_active', 'is_registration_complete', 'date_joined', 'last_login_at',
            'avatar_url',
        ]

    def get_avatar_url(self, obj) -> str | None:
        if not obj.avatar:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.avatar.url) if request else obj.avatar.url


def _person(user):
    if user is None:
        return None
    return {'id': str(user.id), 'full_name': user.full_name, 'phone': user.phone}


class DirectorTransactionSerializer(TransactionSerializer):
    """Строка журнала для панели: кто клиент и кто провёл операцию.

    Исполнитель хранится как actor_id без FK, поэтому пользователей-исполнителей
    view подгружает одним запросом и передаёт в context['actors'].
    """

    user = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()

    class Meta(TransactionSerializer.Meta):
        fields = TransactionSerializer.Meta.fields + ['user', 'actor', 'actor_id']

    def get_user(self, obj) -> dict | None:
        return _person(obj.user)

    def get_actor(self, obj) -> dict | None:
        if not obj.actor_id:
            return None
        return _person(self.context.get('actors', {}).get(obj.actor_id))


class AuditLogSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            'id', 'actor_type', 'actor_id', 'actor_phone', 'action',
            'entity_type', 'entity_id', 'organization', 'organization_name',
            'before', 'after', 'reason', 'ip_address', 'request_id', 'created_at',
        ]


__all__ = [
    'OrganizationCreateSerializer', 'OrganizationUpdateSerializer', 'BlockSerializer',
    'CreateOrganizationAdminSerializer', 'DirectorUserSerializer', 'AuditLogSerializer',
    'DirectorTransactionSerializer',
    'VisitProgramSerializer', 'CashbackProgramSerializer',
]

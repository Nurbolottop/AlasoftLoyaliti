from rest_framework import serializers

from apps.audit.models import AuditLog
from apps.common.phone import normalize_phone
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

    class Meta:
        model = User
        fields = [
            'id', 'phone', 'first_name', 'last_name', 'full_name', 'public_code',
            'language', 'is_active', 'is_registration_complete', 'date_joined', 'last_login_at',
        ]


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
    'VisitProgramSerializer', 'CashbackProgramSerializer',
]

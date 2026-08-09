from rest_framework import serializers

from apps.organizations.models import (
    CashbackProgram,
    Category,
    LoyaltyType,
    Organization,
    VisitProgram,
)


class CategorySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'slug', 'name', 'name_ru', 'name_ky', 'icon', 'sort_order']

    def get_name(self, obj) -> str:
        return obj.name(self.context.get('language', 'ru'))


class VisitProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisitProgram
        fields = [
            'target_visits', 'reward_count', 'reward_title_ru', 'reward_title_ky', 'is_active',
        ]


class CashbackProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashbackProgram
        fields = [
            'cashback_rate_bps', 'max_spend_percent_bps', 'expiry_days',
            'min_purchase_amount', 'is_active',
        ]


class OrganizationPublicSerializer(serializers.ModelSerializer):
    """Карточка каталога для пользователя (ТЗ общее §9)."""

    category = CategorySerializer(read_only=True)
    description = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    program = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'slug', 'logo_url', 'category', 'description',
            'phone', 'address', 'working_hours', 'loyalty_type', 'program',
        ]

    def _language(self):
        return self.context.get('language', 'ru')

    def get_description(self, obj) -> str:
        return obj.description(self._language())

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url

    def get_program(self, obj) -> dict | None:
        from apps.loyalty.services.state import program_summary
        return program_summary(obj, self._language())


class OrganizationAdminWriteSerializer(serializers.ModelSerializer):
    """Создание/редактирование организации директором."""

    visit_program = VisitProgramSerializer(required=False)
    cashback_program = CashbackProgramSerializer(required=False)

    class Meta:
        model = Organization
        fields = [
            'name', 'slug', 'logo', 'category', 'description_ru', 'description_ky',
            'phone', 'address', 'working_hours', 'loyalty_type',
            'visit_program', 'cashback_program',
        ]

    def validate(self, attrs):
        loyalty_type = attrs.get('loyalty_type') or getattr(self.instance, 'loyalty_type', None)
        if loyalty_type == LoyaltyType.VISIT and attrs.get('cashback_program'):
            raise serializers.ValidationError(
                {'cashback_program': 'Организация работает по программе VISIT'}
            )
        if loyalty_type == LoyaltyType.CASHBACK and attrs.get('visit_program'):
            raise serializers.ValidationError(
                {'visit_program': 'Организация работает по программе CASHBACK'}
            )
        return attrs


class OrganizationDirectorSerializer(serializers.ModelSerializer):
    """Полная карточка для панели директора."""

    category = CategorySerializer(read_only=True)
    visit_program = VisitProgramSerializer(read_only=True)
    cashback_program = CashbackProgramSerializer(read_only=True)
    logo_url = serializers.SerializerMethodField()
    admin = serializers.SerializerMethodField()
    customers_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'slug', 'logo_url', 'category', 'description_ru', 'description_ky',
            'phone', 'address', 'working_hours', 'loyalty_type', 'status',
            'blocked_at', 'blocked_reason', 'visit_program', 'cashback_program',
            'admin', 'customers_count', 'created_at', 'updated_at',
        ]

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url

    def get_admin(self, obj) -> dict | None:
        membership = obj.admins.filter(is_active=True).select_related('user').first()
        if membership is None:
            return None
        return {
            'id': str(membership.user_id),
            'phone': membership.user.phone,
            'full_name': membership.user.full_name,
            'has_pin': membership.user.has_pin,
            'is_active': membership.user.is_active,
            'created_at': membership.created_at,
        }

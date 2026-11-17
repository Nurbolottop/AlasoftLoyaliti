from rest_framework import serializers

from apps.common.files import check_image
from apps.organizations.hours import distance_km, is_open_now, normalize_working_hours, today_hours
from apps.organizations.models import (
    CashbackProgram,
    CatalogItem,
    CatalogSection,
    Category,
    EmployeePosition,
    LoyaltyType,
    Organization,
    OrganizationAdmin,
    VisitProgram,
)


def absolute_url(file_field, request=None):
    if not file_field:
        return None
    return request.build_absolute_uri(file_field.url) if request else file_field.url


class WorkingHoursField(serializers.JSONField):
    def to_internal_value(self, data):
        try:
            return normalize_working_hours(super().to_internal_value(data))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))


class TagsField(serializers.ListField):
    child = serializers.CharField(max_length=30)

    def __init__(self, **kwargs):
        kwargs.setdefault('max_length', 10)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        seen, result = set(), []
        for value in values:
            value = value.strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                result.append(value)
        return result


class CategorySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    # Приходит из annotate во вьюхе: сколько активных организаций в категории.
    organizations_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = [
            'id', 'slug', 'name', 'name_ru', 'name_ky', 'icon', 'sort_order',
            'organizations_count',
        ]

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
    """Публичная карточка заведения (BACKEND_TZ 3.2).

    Контекст: ``language``, ``request``, ``member_ids`` и ``favorite_ids``
    (множества id заведений текущего покупателя; гостю — пустые),
    ``lat``/``lng`` покупателя для distance_km, ``with_program``.
    """

    category = CategorySerializer(read_only=True)
    description = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    program = serializers.SerializerMethodField()
    instagram_url = serializers.CharField(read_only=True)
    latitude = serializers.FloatField(read_only=True, allow_null=True)
    longitude = serializers.FloatField(read_only=True, allow_null=True)
    distance_km = serializers.SerializerMethodField()
    is_open_now = serializers.SerializerMethodField()
    today_hours = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()
    is_member = serializers.SerializerMethodField()
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'slug', 'logo_url', 'cover_url', 'category', 'kind', 'description',
            'phone', 'address', 'latitude', 'longitude', 'distance_km',
            'instagram_url', 'website', 'working_hours', 'today_hours', 'is_open_now', 'tags',
            'rating', 'reviews_count', 'loyalty_type', 'program',
            'card_title', 'card_description', 'showcase_title', 'status',
            'is_member', 'is_favorite',
        ]

    def get_fields(self):
        fields = super().get_fields()
        if self.context.get('with_program') is False:
            fields.pop('program', None)
        return fields

    def _language(self):
        return self.context.get('language', 'ru')

    def get_description(self, obj) -> str:
        return obj.description(self._language())

    def get_logo_url(self, obj) -> str | None:
        return absolute_url(obj.logo, self.context.get('request'))

    def get_cover_url(self, obj) -> str | None:
        return absolute_url(obj.cover, self.context.get('request'))

    def get_program(self, obj) -> dict | None:
        from apps.loyalty.services.state import program_summary
        return program_summary(obj, self._language())

    def get_distance_km(self, obj) -> float | None:
        return distance_km(self.context.get('lat'), self.context.get('lng'), obj.latitude, obj.longitude)

    def get_is_open_now(self, obj) -> bool | None:
        return is_open_now(obj.working_hours)

    def get_today_hours(self, obj) -> dict | None:
        return today_hours(obj.working_hours)

    def get_rating(self, obj) -> float | None:
        return None   # отзывов пока нет — блок в приложении скрыт (открытый вопрос 3)

    def get_reviews_count(self, obj) -> int:
        return 0

    def get_is_member(self, obj) -> bool:
        return obj.id in self.context.get('member_ids', set())

    def get_is_favorite(self, obj) -> bool:
        return obj.id in self.context.get('favorite_ids', set())


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
        membership = (
            obj.admins.filter(is_active=True, deleted_at__isnull=True, position=EmployeePosition.ADMIN)
            .select_related('user').order_by('created_at').first()
        )
        if membership is None:
            return None
        return {
            'id': str(membership.user_id),
            'phone': membership.user.phone,
            'full_name': membership.display_name,
            'has_pin': membership.user.has_pin,
            'is_active': membership.user.is_active,
            'created_at': membership.created_at,
        }


class OrganizationProgramSerializer(serializers.Serializer):
    """Настройка программы владельцем заведения (PATCH /admin/program).

    Организация НЕ принимается из тела — она берётся из привязки
    администратора на сервере (ТЗ backend §3).
    """

    loyalty_type = serializers.ChoiceField(choices=LoyaltyType.choices, required=False)
    visit_program = VisitProgramSerializer(required=False)
    cashback_program = CashbackProgramSerializer(required=False)
    # Текст на карте (7.12) — сохраняется в организацию
    card_title = serializers.CharField(max_length=30, required=False, allow_blank=True)
    card_description = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate(self, attrs):
        target_type = attrs.get('loyalty_type') or self.context.get('current_loyalty_type')
        if target_type == LoyaltyType.VISIT and attrs.get('cashback_program'):
            raise serializers.ValidationError(
                {'cashback_program': 'Организация работает по программе VISIT'}
            )
        if target_type == LoyaltyType.CASHBACK and attrs.get('visit_program'):
            raise serializers.ValidationError(
                {'visit_program': 'Организация работает по программе CASHBACK'}
            )
        if not attrs:
            raise serializers.ValidationError('Нечего обновлять')
        return attrs


class OrganizationSelfSerializer(serializers.ModelSerializer):
    """Своя организация глазами её администратора (GET/PATCH /admin/organization).

    slug не отдаём — он в публичных ссылках; loyalty_type правится через
    /admin/program, status и блокировка остаются за директором.
    """

    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source='category',
        queryset=Category.objects.filter(is_active=True),
        write_only=True, required=False, allow_null=True,
    )
    logo = serializers.ImageField(write_only=True, required=False, allow_null=True)
    logo_url = serializers.SerializerMethodField()
    cover = serializers.ImageField(write_only=True, required=False, allow_null=True)
    cover_url = serializers.SerializerMethodField()
    description_ru = serializers.CharField(max_length=200, required=False, allow_blank=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True,
                                        min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True,
                                         min_value=-180, max_value=180)
    working_hours = WorkingHoursField(required=False)
    tags = TagsField(required=False)

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'logo', 'logo_url', 'cover', 'cover_url', 'category', 'category_id', 'kind',
            'description_ru', 'description_ky', 'phone', 'address', 'latitude', 'longitude',
            'instagram', 'website', 'working_hours', 'tags',
            'card_title', 'card_description', 'showcase_title', 'loyalty_type', 'status',
        ]
        read_only_fields = ['id', 'loyalty_type', 'status']

    def get_logo_url(self, obj) -> str | None:
        return absolute_url(obj.logo, self.context.get('request'))

    def get_cover_url(self, obj) -> str | None:
        return absolute_url(obj.cover, self.context.get('request'))

    def validate_logo(self, value):
        return check_image(value)

    def validate_cover(self, value):
        return check_image(value)

    def update(self, instance, validated_data):
        # «"logo": null» снимает картинку: удаляем и сам файл
        for field in ('logo', 'cover'):
            if field in validated_data and validated_data[field] is None and getattr(instance, field):
                getattr(instance, field).delete(save=False)
        return super().update(instance, validated_data)


class CatalogSectionSerializer(serializers.ModelSerializer):
    items_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = CatalogSection
        fields = ['id', 'name', 'description', 'items_count', 'sort_order']
        read_only_fields = ['id']


class CatalogItemSerializer(serializers.ModelSerializer):
    """Позиция витрины (BACKEND_TZ 3.7). is_favorite — только покупателю."""

    section_id = serializers.UUIDField(read_only=True)
    organization_id = serializers.UUIDField(read_only=True)
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    price = serializers.IntegerField(min_value=1, help_text='Тыйын')

    class Meta:
        model = CatalogItem
        fields = [
            'id', 'section_id', 'organization_id', 'name', 'price', 'image', 'image_url',
            'short_description', 'description', 'composition', 'volume', 'duration', 'sort_order',
        ]
        read_only_fields = ['id']

    def get_fields(self):
        fields = super().get_fields()
        if 'favorite_ids' in self.context:
            fields['is_favorite'] = serializers.SerializerMethodField()
        return fields

    def get_image_url(self, obj) -> str | None:
        return absolute_url(obj.image, self.context.get('request'))

    def get_is_favorite(self, obj) -> bool:
        return obj.id in self.context.get('favorite_ids', set())

    def validate_image(self, value):
        return check_image(value)

    def update(self, instance, validated_data):
        if 'image' in validated_data and validated_data['image'] is None and instance.image:
            instance.image.delete(save=False)
        return super().update(instance, validated_data)


class ServiceBriefSerializer(serializers.ModelSerializer):
    """Чипы «Стрижка · 500 сом» в «Начислить бонус» (7.5)."""

    class Meta:
        model = CatalogItem
        fields = ['id', 'name', 'price']


class EmployeeSerializer(serializers.ModelSerializer):
    """Сотрудник заведения (BACKEND_TZ 3.8)."""

    user_id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(source='display_name', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    avatar_url = serializers.SerializerMethodField()
    added_at = serializers.DateTimeField(source='created_at', read_only=True)
    can_accept_payment = serializers.SerializerMethodField()
    can_award_bonuses = serializers.SerializerMethodField()
    can_scan = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationAdmin
        fields = [
            'id', 'user_id', 'full_name', 'position', 'phone', 'avatar_url', 'is_active', 'added_at',
            'can_accept_payment', 'can_award_bonuses', 'can_scan',
        ]

    def get_avatar_url(self, obj) -> str | None:
        request = self.context.get('request')
        return absolute_url(obj.avatar, request) or absolute_url(obj.user.avatar, request)

    # У администратора все права включены всегда (5.1)
    def get_can_accept_payment(self, obj) -> bool:
        return obj.has_permission('can_accept_payment')

    def get_can_award_bonuses(self, obj) -> bool:
        return obj.has_permission('can_award_bonuses')

    def get_can_scan(self, obj) -> bool:
        return obj.has_permission('can_scan')


class EmployeeWriteSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    position = serializers.ChoiceField(choices=EmployeePosition.choices)
    phone = serializers.CharField(max_length=20)
    can_accept_payment = serializers.BooleanField(required=False, default=False)
    can_award_bonuses = serializers.BooleanField(required=False, default=False)
    can_scan = serializers.BooleanField(required=False, default=False)
    is_active = serializers.BooleanField(required=False, default=True)
    avatar = serializers.ImageField(required=False, allow_null=True)

    def validate_phone(self, value):
        from apps.common.phone import normalize_phone
        return normalize_phone(value)

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Укажите ФИО')
        return value

    def validate_avatar(self, value):
        return check_image(value)


class ProgramOrganizationBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.CharField()


class ProgramSettingsSerializer(serializers.Serializer):
    """Ответ GET/PATCH /admin/program — описан явно, чтобы попасть в схему."""

    organization = ProgramOrganizationBriefSerializer()
    loyalty_type = serializers.ChoiceField(choices=LoyaltyType.choices)
    visit_program = VisitProgramSerializer(allow_null=True)
    cashback_program = CashbackProgramSerializer(allow_null=True)
    can_change_loyalty_type = serializers.BooleanField()
    card_title = serializers.CharField()
    card_description = serializers.CharField()

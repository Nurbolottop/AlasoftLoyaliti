from rest_framework import serializers

from apps.common.money import tiyin_to_som_str
from apps.loyalty.models import (
    CashbackLot,
    Gift,
    RedemptionRequest,
    Transaction,
    UserOrganizationState,
)
from apps.organizations.models import LoyaltyType

# Человекочитаемые подписи истории (ТЗ общее §14)
HISTORY_LABELS = {
    'VISIT_EARN': {'ru': '+1 посещение', 'ky': '+1 баруу'},
    'GIFT_CREATED': {'ru': 'Получен подарок', 'ky': 'Белек алынды'},
    'GIFT_REDEEM': {'ru': 'Подарок использован', 'ky': 'Белек колдонулду'},
    'CASHBACK_EARN': {'ru': '+{amount} сом', 'ky': '+{amount} сом'},
    'CASHBACK_SPEND': {'ru': '−{amount} сом', 'ky': '−{amount} сом'},
    'CASHBACK_EXPIRE': {'ru': 'Сгорело {amount} сом', 'ky': '{amount} сом күйдү'},
    'REVERSAL': {'ru': 'Операция отменена', 'ky': 'Операция жокко чыгарылды'},
}


# Подпись операции без услуги (3.4): что видит человек в истории
OPERATION_LABELS = {
    'VISIT_EARN': {'ru': 'Отметка добавлена', 'ky': 'Белги кошулду'},
    'GIFT_CREATED': {'ru': 'Подарок получен', 'ky': 'Белек алынды'},
    'GIFT_REDEEM': {'ru': 'Подарок использован', 'ky': 'Белек колдонулду'},
    'CASHBACK_EARN': {'ru': 'Начисление бонусов', 'ky': 'Бонус кошулду'},
    'CASHBACK_SPEND': {'ru': 'Оплата бонусами', 'ky': 'Бонус менен төлөм'},
    'CASHBACK_EXPIRE': {'ru': 'Бонусы сгорели', 'ky': 'Бонустар күйдү'},
    'REVERSAL': {'ru': 'Операция отменена', 'ky': 'Операция жокко чыгарылды'},
}


def signed_amount(tx) -> int | None:
    """Сумма со знаком, как увидит человек: начисление «+», списание «−».

    Визит и подарок считаются штуками: отметка +1, использованный подарок −1.
    """
    amount = tx.amount
    if tx.type in ('VISIT_EARN', 'GIFT_CREATED'):
        return 1
    if tx.type == 'GIFT_REDEEM':
        return -1
    if tx.type == 'CASHBACK_EARN':
        return int(amount or 0)
    if tx.type in ('CASHBACK_SPEND', 'CASHBACK_EXPIRE'):
        return -abs(int(amount or 0))
    if tx.type == 'REVERSAL':
        if amount is not None:
            return int(amount)   # отмена кешбэка хранится уже со знаком
        original_type = (tx.metadata or {}).get('original_type')
        return 1 if original_type == 'GIFT_REDEEM' else -1
    return amount


def operation_label(tx, language='ru') -> str:
    if tx.service_name and tx.type != 'REVERSAL':
        return tx.service_name
    labels = OPERATION_LABELS.get(tx.type, {})
    return labels.get(language) or labels.get('ru') or tx.get_type_display()


class CategoryBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    icon = serializers.CharField()


class OrganizationBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    loyalty_type = serializers.CharField()
    logo_url = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()

    def get_logo_url(self, obj) -> str | None:
        if not getattr(obj, 'logo', None):
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url

    def get_category(self, obj) -> dict | None:
        category = getattr(obj, 'category', None)
        if category is None:
            return None
        return {'id': str(category.id), 'slug': category.slug, 'icon': category.icon}


class TransactionSerializer(serializers.ModelSerializer):
    organization = OrganizationBriefSerializer(read_only=True)
    label = serializers.SerializerMethodField()
    amount_som = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'type', 'status', 'amount', 'amount_som', 'label',
            'organization', 'actor_type', 'related_transaction',
            'reason', 'metadata', 'created_at',
        ]

    def get_amount_som(self, obj) -> str | None:
        return tiyin_to_som_str(obj.amount) if obj.amount is not None else None

    def get_label(self, obj) -> str:
        language = self.context.get('language', 'ru')
        template = HISTORY_LABELS.get(obj.type, {}).get(language, obj.get_type_display())
        return template.format(amount=tiyin_to_som_str(abs(obj.amount or 0)))


class OperationSerializer(serializers.ModelSerializer):
    """Операция для мобильных приложений (BACKEND_TZ 3.4).

    ``amount`` — со знаком, ``label`` — название услуги или подпись типа на
    языке запроса. Сотрудник и ``can_reverse`` отдаются только в /admin/*
    (context['for_staff']=True): покупателю они не нужны.
    """

    organization = OrganizationBriefSerializer(read_only=True)
    amount = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    amount_som = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'type', 'status', 'amount', 'amount_som', 'label',
            'organization', 'service', 'cash_paid', 'actor_type',
            'related_transaction', 'reason', 'created_at',
        ]

    def get_fields(self):
        fields = super().get_fields()
        if self.context.get('for_staff'):
            fields['employee'] = serializers.SerializerMethodField()
            fields['can_reverse'] = serializers.SerializerMethodField()
            fields['customer_id'] = serializers.UUIDField(source='user_id', read_only=True)
        return fields

    def get_amount(self, obj) -> int | None:
        return signed_amount(obj)

    def get_amount_som(self, obj) -> str | None:
        return tiyin_to_som_str(obj.amount) if obj.amount is not None else None

    def get_label(self, obj) -> str:
        return operation_label(obj, self.context.get('language', 'ru'))

    def get_service(self, obj) -> dict | None:
        if not obj.service_name:
            return None
        return {'id': str(obj.service_id) if obj.service_id else None, 'name': obj.service_name}

    def get_employee(self, obj) -> dict | None:
        if not obj.employee_id and not obj.employee_name:
            return None
        return {'id': str(obj.employee_id) if obj.employee_id else None, 'full_name': obj.employee_name}

    def get_can_reverse(self, obj) -> bool:
        from apps.loyalty.services.reversal import can_reverse
        return can_reverse(obj)


class GiftSerializer(serializers.ModelSerializer):
    organization = OrganizationBriefSerializer(read_only=True)
    title = serializers.SerializerMethodField()

    class Meta:
        model = Gift
        fields = ['id', 'status', 'title', 'title_ru', 'title_ky', 'organization', 'created_at', 'used_at']

    def get_title(self, obj) -> str:
        return obj.title(self.context.get('language', 'ru'))


class CashbackLotSerializer(serializers.ModelSerializer):
    organization = OrganizationBriefSerializer(read_only=True)
    remaining_som = serializers.SerializerMethodField()

    class Meta:
        model = CashbackLot
        fields = [
            'id', 'organization', 'original_amount', 'remaining_amount', 'remaining_som',
            'status', 'earned_at', 'expires_at',
        ]

    def get_remaining_som(self, obj) -> str:
        return tiyin_to_som_str(obj.remaining_amount)


class RedemptionRequestSerializer(serializers.ModelSerializer):
    organization = OrganizationBriefSerializer(read_only=True)
    gift = GiftSerializer(read_only=True)
    expires_in = serializers.SerializerMethodField()

    class Meta:
        model = RedemptionRequest
        fields = [
            'id', 'type', 'status', 'organization', 'gift',
            'purchase_total', 'spend_amount', 'cash_paid', 'earn_amount',
            'expires_at', 'expires_in', 'created_at', 'resolved_at',
        ]

    def get_expires_in(self, obj) -> int:
        from django.utils import timezone
        return max(0, int((obj.expires_at - timezone.now()).total_seconds()))


class LoyaltyCardSerializer(serializers.Serializer):
    """Карточка «Мои карты»: VISIT — X/N, CASHBACK — баланс и сгорание."""

    organization = OrganizationBriefSerializer()
    loyalty_type = serializers.CharField()
    visit_progress = serializers.IntegerField(required=False)
    target_visits = serializers.IntegerField(required=False)
    visits_left = serializers.IntegerField(required=False)
    available_gifts = serializers.IntegerField(required=False)
    cashback_available = serializers.IntegerField(required=False)
    cashback_available_som = serializers.CharField(required=False)
    cashback_rate_bps = serializers.IntegerField(required=False)
    max_spend_percent_bps = serializers.IntegerField(required=False)
    next_expiry = serializers.DictField(required=False, allow_null=True)
    last_activity_at = serializers.DateTimeField(allow_null=True)


def build_loyalty_card(state, *, request=None, language='ru'):
    from apps.loyalty.services import cashback as cashback_service

    organization = state.organization
    context = {'request': request, 'language': language}
    card = {
        'organization': OrganizationBriefSerializer(organization, context=context).data,
        'loyalty_type': organization.loyalty_type,
        'last_activity_at': state.last_activity_at,
    }

    card.update({
        'card_title': organization.card_title,
        'card_description': organization.card_description,
        'joined_at': state.joined_at,
        'organization_status': organization.status,
    })

    if organization.loyalty_type == LoyaltyType.VISIT:
        program = getattr(organization, 'visit_program', None)
        target = program.target_visits if program else 0
        card.update({
            'visit_progress': state.visit_progress,
            'target_visits': target,
            'visits_left': max(0, target - state.visit_progress) if target else 0,
            'reward_count': program.reward_count if program else 0,
            'reward_title': program.reward_title(language) if program else '',
            'available_gifts': state.available_gifts,
        })
    else:
        program = getattr(organization, 'cashback_program', None)
        available = cashback_service.available_amount(state.user, organization)
        card.update({
            'cashback_available': available,
            'cashback_available_som': tiyin_to_som_str(available),
            'cashback_rate_bps': program.cashback_rate_bps if program else 0,
            'max_spend_percent_bps': program.max_spend_percent_bps if program else 0,
            'min_purchase_amount': program.min_purchase_amount if program else 0,
            'expiry_days': program.expiry_days if program else 0,
            'next_expiry': cashback_service.next_expiry(state.user, organization),
        })
    return card


def is_regular_customer(user, organization, *, now=None):
    """«Постоянный клиент»: N оплаченных визитов за последние M дней (3.9)."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    now = now or timezone.now()
    paid = Transaction.objects.filter(
        user=user, organization=organization, status='COMPLETED', cash_paid__isnull=False,
        created_at__gte=now - timedelta(days=settings.REGULAR_CUSTOMER_WINDOW_DAYS),
    ).count()
    return paid >= settings.REGULAR_CUSTOMER_MIN_VISITS


def customer_payload(user, state, organization, *, request=None, show_phone=True):
    """Покупатель глазами заведения (BACKEND_TZ 3.9)."""
    from apps.common.phone import mask_phone
    from apps.users.serializers import avatar_url

    return {
        'id': str(user.id),
        'full_name': user.full_name,
        'first_name': user.first_name,
        'phone': user.phone if show_phone else '',
        'phone_masked': mask_phone(user.phone),
        'avatar_url': avatar_url(user, request),
        'gender': user.gender,
        'birth_date': user.birth_date,
        'public_code': user.public_code,
        'joined_at': getattr(state, 'joined_at', None),
        'last_visit_at': getattr(state, 'last_activity_at', None),
        'note': getattr(state, 'note', '') or '',
        'is_regular': is_regular_customer(user, organization),
    }


class UserStateSerializer(serializers.ModelSerializer):
    """Состояние клиента для экрана администратора."""

    class Meta:
        model = UserOrganizationState
        fields = [
            'visit_progress', 'total_visits', 'available_gifts',
            'total_gifts_earned', 'total_gifts_used',
            'cashback_available', 'cashback_total_earned',
            'cashback_total_spent', 'cashback_total_expired',
            'first_activity_at', 'last_activity_at',
        ]


# --- входные схемы admin API -------------------------------------------------

class ResolveQrSerializer(serializers.Serializer):
    qr = serializers.CharField(max_length=256)


class ResolveCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)


class VisitEarnSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    service_id = serializers.UUIDField(required=False, allow_null=True)
    purchase_total = serializers.IntegerField(min_value=0, required=False, allow_null=True,
                                              help_text='Тыйын; по умолчанию — цена услуги')


class CashbackQuoteSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1, help_text='Тыйын')
    spend_amount = serializers.IntegerField(min_value=0, required=False, default=0)


class CashbackEarnSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1)
    service_id = serializers.UUIDField(required=False, allow_null=True)


class CashbackRedeemRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1)
    spend_amount = serializers.IntegerField(min_value=1)
    service_id = serializers.UUIDField(required=False, allow_null=True)


class GiftRedeemRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    service_id = serializers.UUIDField(required=False, allow_null=True)


class CustomerNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=300, allow_blank=True, trim_whitespace=True)


class ReverseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class DirectorReverseSerializer(ReverseSerializer):
    force = serializers.BooleanField(required=False, default=False)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

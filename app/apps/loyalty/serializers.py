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


class OrganizationBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    loyalty_type = serializers.CharField()
    logo_url = serializers.SerializerMethodField()

    def get_logo_url(self, obj) -> str | None:
        if not getattr(obj, 'logo', None):
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url


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

    if organization.loyalty_type == LoyaltyType.VISIT:
        program = getattr(organization, 'visit_program', None)
        target = program.target_visits if program else 0
        card.update({
            'visit_progress': state.visit_progress,
            'target_visits': target,
            'visits_left': max(0, target - state.visit_progress) if target else 0,
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
            'next_expiry': cashback_service.next_expiry(state.user, organization),
        })
    return card


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


class CashbackQuoteSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1, help_text='Тыйын')
    spend_amount = serializers.IntegerField(min_value=0, required=False, default=0)


class CashbackEarnSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1)


class CashbackRedeemRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    purchase_total = serializers.IntegerField(min_value=1)
    spend_amount = serializers.IntegerField(min_value=1)


class GiftRedeemRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()


class ReverseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class DirectorReverseSerializer(ReverseSerializer):
    force = serializers.BooleanField(required=False, default=False)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

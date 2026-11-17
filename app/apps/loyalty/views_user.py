"""USER API: карты, история, подарки, cashback, подтверждения (ТЗ backend §19)."""

from django.conf import settings
from django.db.models import F, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsUser
from apps.common.request_context import request_language
from apps.loyalty.models import (
    CashbackLot,
    CashbackLotStatus,
    Gift,
    GiftStatus,
    RedemptionRequest,
    RedemptionStatus,
    Transaction,
    UserOrganizationState,
)
from apps.loyalty.serializers import (
    CashbackLotSerializer,
    GiftSerializer,
    OperationSerializer,
    RedemptionRequestSerializer,
    RejectSerializer,
    build_loyalty_card,
)
from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services import redemptions as redemption_service
from apps.organizations.models import LoyaltyType


class UserScopedView:
    permission_classes = [IsUser]

    def language(self):
        return request_language(self.request)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['language'] = self.language()
        return context


def card_states(user):
    """Карты покупателя: заведения, где он участник, или есть прогресс/баланс.

    Сначала карты с последней активностью; присоединившиеся без операций —
    в конце, по дате присоединения (6.1).
    """
    return (
        UserOrganizationState.objects.filter(user=user)
        .filter(
            Q(joined_at__isnull=False) | Q(visit_progress__gt=0) | Q(available_gifts__gt=0)
            | Q(cashback_available__gt=0) | Q(total_visits__gt=0)
        )
        .select_related(
            'organization', 'organization__category',
            'organization__visit_program', 'organization__cashback_program',
        )
        .order_by(F('last_activity_at').desc(nulls_last=True), '-joined_at')
    )


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT,
               parameters=[OpenApiParameter('organization_id', str, description='Карта в одном заведении')])
class MyLoyaltyView(UserScopedView, APIView):
    """«Мои карты»; с organization_id — карта в одном заведении (6.5)."""

    def get(self, request):
        states = card_states(request.user)
        organization_id = request.query_params.get('organization_id')
        if organization_id:
            states = states.filter(organization_id=organization_id)

        cards = [build_loyalty_card(state, request=request, language=self.language()) for state in states]

        summary = {
            'organizations_count': len(cards),
            'available_gifts_total': sum(c.get('available_gifts', 0) for c in cards),
            # Cashback разных организаций НЕ складывается в общий кошелёк (ТЗ §10):
            # отдаём разбивку по организациям, а не одну сумму.
            'cashback_by_organization': [
                {
                    'organization_id': c['organization']['id'],
                    'organization_name': c['organization']['name'],
                    'amount': c.get('cashback_available', 0),
                }
                for c in cards if c['loyalty_type'] == LoyaltyType.CASHBACK
            ],
        }
        return Response({'cards': cards, 'summary': summary})


@extend_schema(tags=['user'], parameters=[
    OpenApiParameter('organization_id', str), OpenApiParameter('type', str),
])
class MyTransactionsView(UserScopedView, ListAPIView):
    """История покупателя (6.7): операции 3.4 без сотрудника."""

    serializer_class = OperationSerializer

    def get_queryset(self):
        queryset = Transaction.objects.filter(user=self.request.user).select_related(
            'organization', 'organization__category')
        organization_id = self.request.query_params.get('organization_id')
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        tx_type = self.request.query_params.get('type')
        if tx_type:
            queryset = queryset.filter(type=tx_type)
        return queryset


@extend_schema(tags=['user'])
class MyGiftsView(UserScopedView, ListAPIView):
    serializer_class = GiftSerializer

    def get_queryset(self):
        queryset = Gift.objects.filter(user=self.request.user).select_related('organization')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        else:
            queryset = queryset.filter(
                status__in=[GiftStatus.AVAILABLE, GiftStatus.PENDING_REDEMPTION]
            )
        return queryset


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class MyCashbackView(UserScopedView, APIView):
    """Балансы cashback по организациям + лоты со сроками сгорания."""

    def get(self, request):
        organization_id = request.query_params.get('organization_id')
        lots = (
            CashbackLot.objects.filter(user=request.user, status=CashbackLotStatus.ACTIVE, remaining_amount__gt=0)
            .select_related('organization')
            .order_by('expires_at')
        )
        if organization_id:
            lots = lots.filter(organization_id=organization_id)

        by_organization = {}
        for lot in lots:
            entry = by_organization.setdefault(str(lot.organization_id), {
                'organization_id': str(lot.organization_id),
                'organization_name': lot.organization.name,
                'available': 0,
                'next_expiry': None,
            })
            entry['available'] += lot.remaining_amount
            if entry['next_expiry'] is None:
                entry['next_expiry'] = {'amount': lot.remaining_amount, 'expires_at': lot.expires_at}

        return Response({
            'organizations': list(by_organization.values()),
            'lots': CashbackLotSerializer(
                lots, many=True, context={'request': request, 'language': self.language()}
            ).data,
        })


@extend_schema(tags=['user'])
class MyPendingRedemptionsView(UserScopedView, ListAPIView):
    serializer_class = RedemptionRequestSerializer

    def get_queryset(self):
        return (
            RedemptionRequest.objects.filter(user=self.request.user, status=RedemptionStatus.PENDING)
            .select_related('organization', 'gift')
            .order_by('-created_at')
        )


@extend_schema(tags=['user'], request=None, responses=OpenApiTypes.OBJECT)
class ConfirmRedemptionView(UserScopedView, APIView):
    """Подтверждение списания. Повторный вызов не создаёт второе списание."""

    def post(self, request, pk):
        result, _ = redemption_service.confirm(redemption_id=pk, user=request.user, request=request)
        redemption = RedemptionRequest.objects.select_related('organization', 'gift').get(pk=pk)
        return Response({
            **result,
            'redemption': RedemptionRequestSerializer(
                redemption, context={'request': request, 'language': self.language()}
            ).data,
        })


@extend_schema(tags=['user'], request=RejectSerializer, responses=OpenApiTypes.OBJECT)
class RejectRedemptionView(UserScopedView, APIView):
    def post(self, request, pk):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result, _ = redemption_service.reject(
            redemption_id=pk, user=request.user,
            reason=serializer.validated_data.get('reason', ''), request=request,
        )
        return Response(result)


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class HomeView(UserScopedView, APIView):
    """Сводка главного экрана (ТЗ общее §10)."""

    def get(self, request):
        from apps.notifications.models import NotificationApp
        from apps.notifications.services import unread_count
        from apps.users.serializers import avatar_url

        user = request.user
        cards = [
            build_loyalty_card(state, request=request, language=self.language())
            for state in card_states(user)[:10]
        ]
        pending = RedemptionRequest.objects.filter(
            user=user, status=RedemptionStatus.PENDING
        ).select_related('organization', 'gift')

        return Response({
            'user': {
                'first_name': user.first_name,
                'avatar_url': avatar_url(user, request),
                'public_code': user.public_code,
                'qr_payload': f'alasoft://u/{user.qr_token}' if user.qr_token else None,
            },
            'available_gifts_total': sum(c.get('available_gifts', 0) for c in cards),
            'cards': cards,
            'pending_redemptions': RedemptionRequestSerializer(
                pending, many=True, context={'request': request, 'language': self.language()}
            ).data,
            'unread_notifications': unread_count(user, NotificationApp.CLIENT),
        })


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class MyReferralView(UserScopedView, APIView):
    """«Друзья»: свой код приглашения и приглашённые (6.9)."""

    def get(self, request):
        from apps.users.serializers import avatar_url

        user = request.user
        code = user.ensure_referral_code()
        friends = user.referrals.filter(deleted_at__isnull=True).order_by('-date_joined')
        return Response({
            'code': code,
            'invite_url': f"{settings.REFERRAL_INVITE_BASE_URL.rstrip('/')}/{code}",
            'invited_count': friends.count(),
            'friends': [
                {
                    'id': str(friend.id),
                    'first_name': friend.first_name,
                    'avatar_url': avatar_url(friend, request),
                    'joined_at': friend.date_joined,
                }
                for friend in friends[:100]
            ],
        })


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class SupportContactsView(APIView):
    """Контакты поддержки для экрана «Контакты» (6.8). Меняются без выпуска приложения."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({
            'phone': settings.SUPPORT_PHONE,
            'whatsapp': settings.SUPPORT_WHATSAPP,
            'instagram_url': settings.SUPPORT_INSTAGRAM_URL,
            'email': settings.SUPPORT_EMAIL,
        })

"""USER API: карты, история, подарки, cashback, подтверждения (ТЗ backend §19)."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsUser
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
    RedemptionRequestSerializer,
    RejectSerializer,
    TransactionSerializer,
    build_loyalty_card,
)
from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services import redemptions as redemption_service
from apps.organizations.models import LoyaltyType


class UserScopedView:
    permission_classes = [IsUser]

    def language(self):
        return getattr(self.request.user, 'language', 'ru')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['language'] = self.language()
        return context


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class MyLoyaltyView(UserScopedView, APIView):
    """«Мои карты»: организации, где есть прогресс, подарок или cashback."""

    def get(self, request):
        states = (
            UserOrganizationState.objects.filter(user=request.user)
            .select_related('organization', 'organization__visit_program', 'organization__cashback_program')
            .order_by('-last_activity_at')
        )

        cards = []
        for state in states:
            organization = state.organization
            has_activity = (
                state.visit_progress > 0 or state.available_gifts > 0
                or state.cashback_available > 0 or state.total_visits > 0
            )
            if not has_activity:
                continue
            cards.append(build_loyalty_card(state, request=request, language=self.language()))

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


@extend_schema(tags=['user'])
class MyTransactionsView(UserScopedView, ListAPIView):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = Transaction.objects.filter(user=self.request.user).select_related('organization')
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
        user = request.user
        states = (
            UserOrganizationState.objects.filter(user=user)
            .select_related('organization', 'organization__visit_program', 'organization__cashback_program')
            .order_by('-last_activity_at')[:10]
        )
        cards = [
            build_loyalty_card(state, request=request, language=self.language())
            for state in states
            if state.visit_progress or state.available_gifts or state.cashback_available or state.total_visits
        ]
        pending = RedemptionRequest.objects.filter(
            user=user, status=RedemptionStatus.PENDING
        ).select_related('organization', 'gift')

        return Response({
            'user': {
                'first_name': user.first_name,
                'public_code': user.public_code,
                'qr_payload': f'alasoft://u/{user.qr_token}' if user.qr_token else None,
            },
            'available_gifts_total': sum(c.get('available_gifts', 0) for c in cards),
            'cards': cards,
            'pending_redemptions': RedemptionRequestSerializer(
                pending, many=True, context={'request': request, 'language': self.language()}
            ).data,
            'unread_notifications': user.notifications.filter(is_read=False).count(),
        })

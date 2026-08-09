"""ADMIN API организации (ТЗ backend §20).

Организация всегда берётся из серверного состояния администратора —
organization_id из тела запроса не принимается.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.errors import DomainError, ErrorCode
from apps.common.idempotency import get_idempotency_key, run_idempotent
from apps.common.permissions import IsOrganizationAdmin, current_organization
from apps.loyalty.models import (
    Gift,
    GiftStatus,
    RedemptionRequest,
    RedemptionStatus,
    Transaction,
    UserOrganizationState,
)
from apps.loyalty.serializers import (
    CashbackEarnSerializer,
    CashbackQuoteSerializer,
    CashbackRedeemRequestSerializer,
    GiftRedeemRequestSerializer,
    RedemptionRequestSerializer,
    RejectSerializer,
    ResolveCodeSerializer,
    ResolveQrSerializer,
    ReverseSerializer,
    TransactionSerializer,
    UserStateSerializer,
    VisitEarnSerializer,
)
from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services import redemptions as redemption_service
from apps.loyalty.services import resolve as resolve_service
from apps.loyalty.services import reversal as reversal_service
from apps.loyalty.services import statistics as statistics_service
from apps.loyalty.services.state import get_state
from apps.loyalty.services.visits import earn_visit
from apps.organizations.models import LoyaltyType
from apps.users.models import Role, User
from apps.users.serializers import UserShortSerializer


class AdminBaseView(APIView):
    permission_classes = [IsOrganizationAdmin]

    @property
    def organization(self):
        return current_organization(self.request)

    @property
    def is_schema_generation(self):
        return getattr(self, 'swagger_fake_view', False)

    def get_client(self, user_id):
        user = User.objects.filter(pk=user_id, role=Role.USER).first()
        if user is None:
            raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не найден', status_code=404)
        return user

    def client_payload(self, user, organization):
        state = get_state(user, organization)
        payload = {
            'customer': UserShortSerializer(user).data,
            'organization': {
                'id': str(organization.id),
                'name': organization.name,
                'loyalty_type': organization.loyalty_type,
            },
            'state': UserStateSerializer(state).data,
        }

        if organization.loyalty_type == LoyaltyType.VISIT:
            program = getattr(organization, 'visit_program', None)
            payload['program'] = {
                'target_visits': program.target_visits if program else 0,
                'reward_count': program.reward_count if program else 0,
            }
            payload['gifts'] = [
                {'id': str(g.id), 'title': g.title_ru, 'status': g.status}
                for g in Gift.objects.filter(
                    user=user, organization=organization,
                    status__in=[GiftStatus.AVAILABLE, GiftStatus.PENDING_REDEMPTION],
                )
            ]
        else:
            program = getattr(organization, 'cashback_program', None)
            payload['program'] = {
                'cashback_rate_bps': program.cashback_rate_bps if program else 0,
                'max_spend_percent_bps': program.max_spend_percent_bps if program else 0,
                'expiry_days': program.expiry_days if program else 0,
            }
            payload['cashback_available'] = cashback_service.available_amount(user, organization)
            payload['next_expiry'] = cashback_service.next_expiry(user, organization)

        return payload


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class ResolveQrView(AdminBaseView):
    serializer_class = ResolveQrSerializer

    def post(self, request):
        serializer = ResolveQrSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = resolve_service.resolve_by_qr(serializer.validated_data['qr'])
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class ResolveCodeView(AdminBaseView):
    serializer_class = ResolveCodeSerializer

    def post(self, request):
        serializer = ResolveCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = resolve_service.resolve_by_code(serializer.validated_data['code'])
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class CustomerStateView(AdminBaseView):
    def get(self, request, pk):
        user = self.get_client(pk)
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], request=VisitEarnSerializer, responses=OpenApiTypes.OBJECT)
class VisitEarnView(AdminBaseView):
    serializer_class = VisitEarnSerializer

    def post(self, request):
        serializer = VisitEarnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        key = get_idempotency_key(request)

        def handler():
            result = earn_visit(
                user=user, organization=organization, admin=request.user,
                idempotency_key=key or '', request=request, note=data.get('note', ''),
            )
            program = result['program']
            return {
                'transaction_id': str(result['transaction'].id),
                'visit_progress': result['state'].visit_progress,
                'target_visits': program.target_visits,
                'visits_left': max(0, program.target_visits - result['state'].visit_progress),
                'available_gifts': result['state'].available_gifts,
                'gifts_created': [
                    {'id': str(g.id), 'title': g.title_ru} for g in result['gifts']
                ],
            }

        body, status_code = run_idempotent(request, 'admin/visits', request.data, handler)
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], request=CashbackQuoteSerializer, responses=OpenApiTypes.OBJECT)
class CashbackQuoteView(AdminBaseView):
    """Серверный расчёт списания и начисления. Ничего не меняет."""

    serializer_class = CashbackQuoteSerializer

    def post(self, request):
        serializer = CashbackQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])

        quote = cashback_service.quote(
            user=user, organization=organization,
            purchase_total=data['purchase_total'],
            requested_spend=data.get('spend_amount', 0),
        )
        return Response(quote)


@extend_schema(tags=['admin'], request=CashbackEarnSerializer, responses=OpenApiTypes.OBJECT)
class CashbackEarnView(AdminBaseView):
    """Покупка без списания — начисление сразу, подтверждение не требуется."""

    serializer_class = CashbackEarnSerializer

    def post(self, request):
        serializer = CashbackEarnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        key = get_idempotency_key(request)

        def handler():
            result = cashback_service.earn_only(
                user=user, organization=organization,
                purchase_total=data['purchase_total'], admin=request.user,
                idempotency_key=key or '', request=request,
            )
            return {
                'transaction_id': str(result['transaction'].id) if result['transaction'] else None,
                'earn_amount': result['quote']['earn_amount'],
                'cash_paid': result['quote']['cash_paid'],
                'cashback_available': cashback_service.available_amount(user, organization),
            }

        body, status_code = run_idempotent(request, 'admin/cashback/earn', request.data, handler)
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], request=CashbackRedeemRequestSerializer, responses=OpenApiTypes.OBJECT)
class CashbackRedeemRequestView(AdminBaseView):
    """Покупка со списанием — только через подтверждение пользователем."""

    serializer_class = CashbackRedeemRequestSerializer

    def post(self, request):
        serializer = CashbackRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        key = get_idempotency_key(request)

        def handler():
            redemption = redemption_service.create_cashback_request(
                user=user, organization=organization,
                purchase_total=data['purchase_total'], spend_amount=data['spend_amount'],
                admin=request.user, idempotency_key=key or '', request=request,
            )
            return {
                'redemption_id': str(redemption.id),
                'status': redemption.status,
                'purchase_total': redemption.purchase_total,
                'spend_amount': redemption.spend_amount,
                'cash_paid': redemption.cash_paid,
                'earn_amount': redemption.earn_amount,
                'expires_at': redemption.expires_at.isoformat(),
            }

        body, status_code = run_idempotent(request, 'admin/cashback/redeem-request', request.data, handler)
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], request=GiftRedeemRequestSerializer, responses=OpenApiTypes.OBJECT)
class GiftRedeemRequestView(AdminBaseView):
    serializer_class = GiftRedeemRequestSerializer

    def post(self, request, pk):
        serializer = GiftRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = self.organization
        user = self.get_client(serializer.validated_data['user_id'])
        key = get_idempotency_key(request)

        def handler():
            redemption = redemption_service.create_gift_request(
                user=user, organization=organization, gift_id=pk,
                admin=request.user, idempotency_key=key or '', request=request,
            )
            return {
                'redemption_id': str(redemption.id),
                'status': redemption.status,
                'gift_id': str(redemption.gift_id),
                'expires_at': redemption.expires_at.isoformat(),
            }

        body, status_code = run_idempotent(
            request, f'admin/gifts/{pk}/redeem-request', request.data, handler
        )
        return Response(body, status=status_code)


@extend_schema(tags=['admin'])
class AdminRedemptionsView(AdminBaseView, ListAPIView):
    serializer_class = RedemptionRequestSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return RedemptionRequest.objects.none()
        queryset = (
            RedemptionRequest.objects.filter(organization=self.organization)
            .select_related('organization', 'gift')
        )
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset


@extend_schema(tags=['admin'], request=RejectSerializer, responses=OpenApiTypes.OBJECT)
class AdminRedemptionCancelView(AdminBaseView):
    serializer_class = RejectSerializer

    def post(self, request, pk):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        redemption = redemption_service.cancel_by_admin(
            redemption_id=pk, organization=self.organization, admin=request.user,
            reason=serializer.validated_data.get('reason', ''), request=request,
        )
        return Response({'redemption_id': str(redemption.id), 'status': redemption.status})


@extend_schema(tags=['admin'])
class AdminTransactionsView(AdminBaseView, ListAPIView):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return Transaction.objects.none()
        queryset = (
            Transaction.objects.filter(organization=self.organization)
            .select_related('organization')
        )
        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        tx_type = self.request.query_params.get('type')
        if tx_type:
            queryset = queryset.filter(type=tx_type)
        return queryset


@extend_schema(tags=['admin'], request=ReverseSerializer, responses=OpenApiTypes.OBJECT)
class AdminReverseView(AdminBaseView):
    serializer_class = ReverseSerializer

    def post(self, request, pk):
        serializer = ReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = self.organization

        def handler():
            result = reversal_service.reverse_transaction(
                transaction_id=pk, actor=request.user,
                reason=serializer.validated_data['reason'],
                organization=organization, force=False, request=request,
            )
            return {
                'original_transaction_id': str(result['original'].id),
                'reversal_transaction_id': str(result['reversal'].id),
                'status': result['original'].status,
            }

        body, status_code = run_idempotent(
            request, f'admin/transactions/{pk}/reverse', request.data, handler, required=False
        )
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminCustomersView(AdminBaseView, ListAPIView):
    """Клиенты организации. Телефон отдаётся маскированным (минимизация PII)."""

    def get(self, request, *args, **kwargs):
        organization = self.organization
        queryset = (
            UserOrganizationState.objects.filter(organization=organization)
            .select_related('user')
            .order_by('-last_activity_at')
        )
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(user__public_code=search) if search.isdigit() and len(search) == 6 else (
                queryset.filter(user__first_name__icontains=search)
            )

        page = self.paginate_queryset(queryset)
        data = [
            {
                **UserShortSerializer(state.user).data,
                'state': UserStateSerializer(state).data,
            }
            for state in page
        ]
        return self.get_paginated_response(data)

    serializer_class = UserStateSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return UserOrganizationState.objects.none()
        return UserOrganizationState.objects.filter(organization=self.organization)


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminStatisticsView(AdminBaseView):
    def get(self, request):
        return Response(statistics_service.organization_statistics(self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminDashboardView(AdminBaseView):
    """Dashboard организации: краткая сводка для главного экрана админа."""

    def get(self, request):
        organization = self.organization
        stats = statistics_service.organization_statistics(organization)
        pending = RedemptionRequest.objects.filter(
            organization=organization, status=RedemptionStatus.PENDING
        ).count()
        return Response({
            'organization': {
                'id': str(organization.id),
                'name': organization.name,
                'loyalty_type': organization.loyalty_type,
                'status': organization.status,
            },
            'pending_redemptions': pending,
            'statistics': stats,
        })

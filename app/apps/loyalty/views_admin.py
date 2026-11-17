"""ADMIN API заведения (ТЗ backend §20, BACKEND_TZ §5, §7).

Организация всегда берётся из записи сотрудника на сервере —
organization_id из тела запроса не принимается. Каждая вью задаёт
``required_permission`` (раздел 5.3): ``ROLE_ADMIN`` — только
администратор, ``ANY_STAFF`` — любой сотрудник, иначе имя права.
"""

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import ConflictError, DomainError, ErrorCode, NotFoundError
from apps.common.idempotency import get_idempotency_key, run_idempotent
from apps.common.permissions import ANY_STAFF, ROLE_ADMIN, IsOrganizationStaff, current_organization
from apps.common.request_context import request_language
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
    CustomerNoteSerializer,
    GiftRedeemRequestSerializer,
    OperationSerializer,
    RedemptionRequestSerializer,
    RejectSerializer,
    ResolveCodeSerializer,
    ResolveQrSerializer,
    ReverseSerializer,
    UserStateSerializer,
    VisitEarnSerializer,
    customer_payload,
)
from apps.loyalty.services import business as business_service
from apps.loyalty.services import cashback as cashback_service
from apps.loyalty.services import redemptions as redemption_service
from apps.loyalty.services import resolve as resolve_service
from apps.loyalty.services import reversal as reversal_service
from apps.loyalty.services import statistics as statistics_service
from apps.loyalty.services.state import CUSTOMER_ROLES, peek_state
from apps.loyalty.services.visits import earn_visit
from apps.notifications.services import unread_count
from apps.organizations.models import CatalogItem, LoyaltyType
from apps.organizations.serializers import (
    EmployeeSerializer,
    OrganizationProgramSerializer,
    OrganizationSelfSerializer,
    ProgramSettingsSerializer,
    absolute_url,
)
from apps.organizations.services import program_payload, update_loyalty_program
from apps.users.models import User
from apps.users.serializers import UserShortSerializer

PERIOD_PARAM = OpenApiParameter('period', int, description='Дней: 1, 7, 15 или 30', enum=[1, 7, 15, 30])


class AdminBaseView(APIView):
    permission_classes = [IsOrganizationStaff]
    required_permission = ROLE_ADMIN

    @property
    def organization(self):
        return current_organization(self.request)

    @property
    def staff(self):
        return getattr(self.request, 'staff', None)

    @property
    def is_admin(self):
        return bool(self.staff and self.staff.is_admin)

    @property
    def is_schema_generation(self):
        return getattr(self, 'swagger_fake_view', False)

    def operation_context(self):
        return {'request': self.request, 'language': request_language(self.request), 'for_staff': True}

    def get_client(self, user_id):
        user = User.objects.filter(pk=user_id, role__in=CUSTOMER_ROLES).first()
        if user is None:
            raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не найден', status_code=404)
        return user

    def get_service(self, service_id):
        """Услуга только из своего каталога, иначе CATALOG_ITEM_NOT_FOUND."""
        if not service_id:
            return None
        service = CatalogItem.objects.filter(pk=service_id, organization=self.organization).first()
        if service is None:
            raise NotFoundError(code=ErrorCode.CATALOG_ITEM_NOT_FOUND, message='Услуга не найдена')
        return service

    def client_payload(self, user, organization):
        """Карточка клиента (BACKEND_TZ 7.4). Скан не делает покупателя клиентом."""
        state = peek_state(user, organization)
        visit = getattr(organization, 'visit_program', None)
        cashback = getattr(organization, 'cashback_program', None)
        payload = {
            'customer': customer_payload(user, state, organization, request=self.request),
            'organization': {
                'id': str(organization.id),
                'name': organization.name,
                'loyalty_type': organization.loyalty_type,
                'status': organization.status,
            },
            'state': UserStateSerializer(state).data,
            'is_member': bool(state.pk and state.joined_at),
            'program': {
                'target_visits': visit.target_visits if visit else 0,
                'reward_count': visit.reward_count if visit else 0,
                'cashback_rate_bps': cashback.cashback_rate_bps if cashback else 0,
                'max_spend_percent_bps': cashback.max_spend_percent_bps if cashback else 0,
                'expiry_days': cashback.expiry_days if cashback else 0,
                'min_purchase_amount': cashback.min_purchase_amount if cashback else 0,
            },
            'gifts': [
                {'id': str(g.id), 'title': g.title(request_language(self.request)), 'status': g.status}
                for g in Gift.objects.filter(
                    user=user, organization=organization,
                    status__in=[GiftStatus.AVAILABLE, GiftStatus.PENDING_REDEMPTION],
                )
            ],
            'next_expiry': None,
        }
        if organization.loyalty_type == LoyaltyType.CASHBACK:
            payload['cashback_available'] = cashback_service.available_amount(user, organization)
            payload['next_expiry'] = cashback_service.next_expiry(user, organization)
        return payload


class AdminListView(AdminBaseView, ListAPIView):
    def get_serializer_context(self):
        context = ListAPIView.get_serializer_context(self)
        context.update(self.operation_context())
        return context


# --- кто вошёл ---------------------------------------------------------------

@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminMeView(AdminBaseView):
    """Сотрудник и его заведение (BACKEND_TZ 5.4)."""

    required_permission = ANY_STAFF

    def get(self, request):
        organization = self.organization
        return Response({
            **EmployeeSerializer(self.staff, context={'request': request}).data,
            'organization': {
                'id': str(organization.id),
                'name': organization.name,
                'logo_url': absolute_url(organization.logo, request),
                'loyalty_type': organization.loyalty_type,
                'status': organization.status,
            },
        })


# --- сканер и карточка клиента -------------------------------------------------

@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class ResolveQrView(AdminBaseView):
    required_permission = 'can_scan'
    serializer_class = ResolveQrSerializer

    def post(self, request):
        serializer = ResolveQrSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = resolve_service.resolve_by_qr(serializer.validated_data['qr'])
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class ResolveCodeView(AdminBaseView):
    required_permission = 'can_scan'
    serializer_class = ResolveCodeSerializer

    def post(self, request):
        serializer = ResolveCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = resolve_service.resolve_by_code(serializer.validated_data['code'])
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class CustomerStateView(AdminBaseView):
    required_permission = 'can_scan'

    def get(self, request, pk):
        user = self.get_client(pk)
        return Response(self.client_payload(user, self.organization))


@extend_schema(tags=['admin'], request=CustomerNoteSerializer, responses=OpenApiTypes.OBJECT)
class CustomerNoteView(AdminBaseView):
    """Заметка заведения о клиенте; видят все сотрудники, покупатель — нет (7.4)."""

    serializer_class = CustomerNoteSerializer

    def patch(self, request, pk):
        serializer = CustomerNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = self.organization
        user = self.get_client(pk)
        state = UserOrganizationState.objects.filter(user=user, organization=organization).first()
        if state is None:
            raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Покупатель не клиент заведения',
                              status_code=404)
        before = state.note
        state.note = serializer.validated_data['note']
        state.save(update_fields=['note', 'updated_at'])
        log_action(AuditAction.CUSTOMER_NOTE_UPDATED, actor=request.user, request=request,
                   entity_type='Customer', entity_id=user.id, organization=organization,
                   before={'note': before}, after={'note': state.note})
        return Response(customer_payload(user, state, organization, request=request))


# --- операции -------------------------------------------------------------------

@extend_schema(tags=['admin'], request=VisitEarnSerializer, responses=OpenApiTypes.OBJECT)
class VisitEarnView(AdminBaseView):
    required_permission = 'can_award_bonuses'
    serializer_class = VisitEarnSerializer

    def post(self, request):
        serializer = VisitEarnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        service = self.get_service(data.get('service_id'))
        key = get_idempotency_key(request)

        def handler():
            result = earn_visit(
                user=user, organization=organization, admin=request.user,
                idempotency_key=key or '', request=request, note=data.get('note', ''),
                employee=self.staff, service=service, purchase_total=data.get('purchase_total'),
            )
            program = result['program']
            return {
                'transaction_id': str(result['transaction'].id),
                'visit_progress': result['state'].visit_progress,
                'target_visits': program.target_visits,
                'visits_left': max(0, program.target_visits - result['state'].visit_progress),
                'available_gifts': result['state'].available_gifts,
                'cash_paid': result['transaction'].cash_paid,
                'gifts_created': [
                    {'id': str(g.id), 'title': g.title_ru} for g in result['gifts']
                ],
            }

        body, status_code = run_idempotent(request, 'admin/visits', request.data, handler)
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], request=CashbackQuoteSerializer, responses=OpenApiTypes.OBJECT)
class CashbackQuoteView(AdminBaseView):
    """Серверный расчёт списания и начисления. Ничего не меняет."""

    required_permission = 'can_award_bonuses'
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

    required_permission = 'can_award_bonuses'
    serializer_class = CashbackEarnSerializer

    def post(self, request):
        serializer = CashbackEarnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        service = self.get_service(data.get('service_id'))
        key = get_idempotency_key(request)

        def handler():
            result = cashback_service.earn_only(
                user=user, organization=organization,
                purchase_total=data['purchase_total'], admin=request.user,
                idempotency_key=key or '', request=request,
                employee=self.staff, service=service,
            )
            return {
                'transaction_id': str(result['transaction'].id) if result['transaction'] else None,
                'earn_amount': result['quote']['earn_amount'],
                'cash_paid': result['quote']['cash_paid'],
                'cashback_available': cashback_service.available_amount(user, organization),
            }

        body, status_code = run_idempotent(request, 'admin/cashback/earn', request.data, handler)
        return Response(body, status=status_code)


def _redemption_body(redemption):
    return {
        'redemption_id': str(redemption.id),
        'status': redemption.status,
        'purchase_total': redemption.purchase_total,
        'spend_amount': redemption.spend_amount,
        'cash_paid': redemption.cash_paid,
        'earn_amount': redemption.earn_amount,
        'gift_id': str(redemption.gift_id) if redemption.gift_id else None,
        'expires_at': redemption.expires_at.isoformat(),
    }


@extend_schema(tags=['admin'], request=CashbackRedeemRequestSerializer, responses=OpenApiTypes.OBJECT)
class CashbackRedeemRequestView(AdminBaseView):
    """Покупка со списанием — только через подтверждение пользователем."""

    required_permission = 'can_accept_payment'
    serializer_class = CashbackRedeemRequestSerializer

    def post(self, request):
        serializer = CashbackRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = self.organization
        user = self.get_client(data['user_id'])
        service = self.get_service(data.get('service_id'))
        key = get_idempotency_key(request)

        def handler():
            redemption = redemption_service.create_cashback_request(
                user=user, organization=organization,
                purchase_total=data['purchase_total'], spend_amount=data['spend_amount'],
                admin=request.user, idempotency_key=key or '', request=request,
                employee=self.staff, service=service,
            )
            return _redemption_body(redemption)

        body, status_code = run_idempotent(request, 'admin/cashback/redeem-request', request.data, handler)
        return Response(body, status=status_code)


@extend_schema(tags=['admin'], request=GiftRedeemRequestSerializer, responses=OpenApiTypes.OBJECT)
class GiftRedeemRequestView(AdminBaseView):
    required_permission = 'can_accept_payment'
    serializer_class = GiftRedeemRequestSerializer

    def post(self, request, pk):
        serializer = GiftRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = self.organization
        user = self.get_client(serializer.validated_data['user_id'])
        service = self.get_service(serializer.validated_data.get('service_id'))
        key = get_idempotency_key(request)

        def handler():
            redemption = redemption_service.create_gift_request(
                user=user, organization=organization, gift_id=pk,
                admin=request.user, idempotency_key=key or '', request=request,
                employee=self.staff, service=service,
            )
            return _redemption_body(redemption)

        body, status_code = run_idempotent(
            request, f'admin/gifts/{pk}/redeem-request', request.data, handler
        )
        return Response(body, status=status_code)


class OwnRedemptionsMixin:
    """Сотрудник без должности ADMIN видит и отменяет только свои запросы."""

    def redemptions(self):
        queryset = (
            RedemptionRequest.objects.filter(organization=self.organization)
            .select_related('organization', 'gift', 'user')
        )
        if not self.is_admin:
            queryset = queryset.filter(created_by_employee=self.staff)
        return queryset


@extend_schema(tags=['admin'], parameters=[OpenApiParameter('status', str)])
class AdminRedemptionsView(OwnRedemptionsMixin, AdminListView):
    required_permission = 'can_accept_payment'
    serializer_class = RedemptionRequestSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return RedemptionRequest.objects.none()
        queryset = self.redemptions()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset


@extend_schema(tags=['admin'], responses=RedemptionRequestSerializer)
class AdminRedemptionDetailView(OwnRedemptionsMixin, AdminBaseView):
    """Один запрос — экран ожидания обновляется по push (7.5)."""

    required_permission = ANY_STAFF

    def get(self, request, pk):
        redemption = self.redemptions().filter(pk=pk).first()
        if redemption is None:
            raise DomainError(code=ErrorCode.REDEMPTION_NOT_FOUND, message='Запрос не найден', status_code=404)
        return Response(RedemptionRequestSerializer(redemption, context=self.operation_context()).data)


@extend_schema(tags=['admin'], request=RejectSerializer, responses=OpenApiTypes.OBJECT)
class AdminRedemptionCancelView(OwnRedemptionsMixin, AdminBaseView):
    required_permission = 'can_accept_payment'
    serializer_class = RejectSerializer

    def post(self, request, pk):
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not self.redemptions().filter(pk=pk).exists():
            raise DomainError(code=ErrorCode.REDEMPTION_NOT_FOUND, message='Запрос не найден', status_code=404)
        redemption = redemption_service.cancel_by_admin(
            redemption_id=pk, organization=self.organization, admin=request.user,
            reason=serializer.validated_data.get('reason', ''), request=request,
        )
        return Response({'redemption_id': str(redemption.id), 'status': redemption.status})


@extend_schema(tags=['admin'], parameters=[
    OpenApiParameter('user_id', str, description='История одного клиента — доступна любому сотруднику'),
    OpenApiParameter('type', str), OpenApiParameter('service_id', str),
])
class AdminTransactionsView(AdminListView):
    serializer_class = OperationSerializer

    def get_required_permission(self, request):
        # История одного клиента — любому сотруднику, весь журнал — администратору
        return ANY_STAFF if request.query_params.get('user_id') else ROLE_ADMIN

    def get_queryset(self):
        if self.is_schema_generation:
            return Transaction.objects.none()
        queryset = (
            Transaction.objects.filter(organization=self.organization)
            .select_related('organization', 'organization__category')
        )
        params = self.request.query_params
        if params.get('user_id'):
            queryset = queryset.filter(user_id=params['user_id'])
        if params.get('type'):
            queryset = queryset.filter(type=params['type'])
        if params.get('service_id'):
            queryset = queryset.filter(service_id=params['service_id'])
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


# --- клиенты ---------------------------------------------------------------------

def _customer_rows(view, states, organization, extra=None):
    """Элемент списка: {customer, last_operation} + прежние плоские поля для старых версий."""
    request = view.request
    last_ops = business_service.last_operations(organization, [state.user_id for state in states])
    context = view.operation_context()
    rows = []
    for state in states:
        user = state.user
        last_op = last_ops.get(user.id)
        row = {
            **UserShortSerializer(user, context={'request': request}).data,
            'state': UserStateSerializer(state).data,
            'customer': customer_payload(user, state, organization, request=request),
            'last_operation': OperationSerializer(last_op, context=context).data if last_op else None,
        }
        if extra:
            row.update(extra(state))
        rows.append(row)
    return rows


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT, parameters=[
    OpenApiParameter('search', str, description='Имя или телефон, от 2 символов'),
    OpenApiParameter('filter', str, enum=['all', 'today', 'new', 'with_bonuses']),
    OpenApiParameter('gender', str, enum=['MALE', 'FEMALE']),
    OpenApiParameter('visited_from', str, description='YYYY-MM-DD'),
    OpenApiParameter('visited_to', str, description='YYYY-MM-DD'),
    OpenApiParameter('ordering', str, enum=['-last_visit_at', 'full_name']),
])
class AdminCustomersView(AdminListView):
    """Клиенты заведения (7.3). Элемент: {customer, last_operation}."""

    serializer_class = UserStateSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return UserOrganizationState.objects.none()
        return business_service.customers_queryset(self.organization, self.request.query_params)

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(_customer_rows(self, page, self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT, parameters=[
    OpenApiParameter('period', int, enum=[1, 7, 30]), OpenApiParameter('search', str),
])
class AdminNewCustomersView(AdminListView):
    """Новые клиенты за период, новые сверху (7.3)."""

    serializer_class = UserStateSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return UserOrganizationState.objects.none()
        period = business_service.parse_period(self.request.query_params.get('period'), default=1)
        start, end, _ = business_service.period_bounds(period)
        params = {'search': self.request.query_params.get('search', '')}
        return (
            business_service.customers_queryset(self.organization, params)
            .filter(joined_at__gte=start, joined_at__lt=end)
            .order_by('-joined_at')
        )

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(_customer_rows(
            self, page, self.organization, extra=lambda state: {'registered_at': state.joined_at},
        ))


# --- отчёты ------------------------------------------------------------------------

@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT, parameters=[PERIOD_PARAM])
class AdminOverviewView(AdminBaseView):
    """Главная и «Статистика» администратора (7.2, 7.7)."""

    def get(self, request):
        organization = self.organization
        period = business_service.parse_period(request.query_params.get('period'), default=1)
        data = business_service.overview(organization, period)
        states = list(
            UserOrganizationState.objects.filter(organization=organization, joined_at__isnull=False)
            .select_related('user').order_by('-last_activity_at')[:5]
        )
        last_ops = business_service.last_operations(organization, [s.user_id for s in states])
        context = self.operation_context()
        return Response({
            'organization': {
                'id': str(organization.id),
                'name': organization.name,
                'logo_url': absolute_url(organization.logo, request),
                'cover_url': absolute_url(organization.cover, request),
                'loyalty_type': organization.loyalty_type,
                'status': organization.status,
            },
            **data,
            'unread_notifications': unread_count(request.user, 'BUSINESS'),
            'recent_customers': [
                {
                    'customer': customer_payload(s.user, s, organization, request=request),
                    'last_operation': (
                        OperationSerializer(last_ops[s.user_id], context=context).data
                        if s.user_id in last_ops else None
                    ),
                }
                for s in states
            ],
        })


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT, parameters=[PERIOD_PARAM])
class AdminRevenueView(AdminBaseView):
    """Выручка за период и график (7.8)."""

    def get(self, request):
        period = business_service.parse_period(request.query_params.get('period'), default=1)
        return Response(business_service.revenue(self.organization, period))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT, parameters=[PERIOD_PARAM])
class AdminPaymentsView(AdminListView):
    """История оплат: оплачено деньгами, без отменённых (7.8)."""

    serializer_class = OperationSerializer

    def get_queryset(self):
        if self.is_schema_generation:
            return Transaction.objects.none()
        period = business_service.parse_period(self.request.query_params.get('period'), default=1)
        start, end, _ = business_service.period_bounds(period)
        return (
            business_service.payments(self.organization, start, end)
            .select_related('user').order_by('-created_at', '-id')
        )

    def list(self, request, *args, **kwargs):
        from apps.loyalty.serializers import operation_label
        from apps.users.serializers import avatar_url

        language = request_language(request)
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response([
            {
                'id': str(tx.id),
                'at': tx.created_at,
                'label': operation_label(tx, language),
                'amount': tx.cash_paid,
                'type': tx.type,
                'customer': {
                    'id': str(tx.user_id),
                    'full_name': tx.user.full_name,
                    'avatar_url': avatar_url(tx.user, request),
                },
            }
            for tx in page
        ])


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminStatisticsView(AdminBaseView):
    """Для старых версий; новые читают /admin/overview."""

    def get(self, request):
        return Response(statistics_service.organization_statistics(self.organization))


@extend_schema(tags=['admin'], responses=OpenApiTypes.OBJECT)
class AdminDashboardView(AdminBaseView):
    """Для старых версий; новые читают /admin/overview."""

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


# --- настройки заведения -----------------------------------------------------------

@extend_schema(tags=['admin'], request=OrganizationProgramSerializer, responses=ProgramSettingsSerializer)
class AdminProgramView(AdminBaseView):
    """Настройки программы своей организации (7.12).

    Читать может любой сотрудник, менять — администратор. Тип программы не
    меняется: он выбирается один раз при создании организации.
    """

    serializer_class = OrganizationProgramSerializer

    def get_required_permission(self, request):
        return ANY_STAFF if request.method == 'GET' else ROLE_ADMIN

    def get(self, request):
        return Response(program_payload(self.organization))

    def patch(self, request):
        organization = self.organization
        serializer = OrganizationProgramSerializer(
            data=request.data,
            context={'current_loyalty_type': organization.loyalty_type},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Тот же тип в теле допустим (старые версии приложения шлют его всегда)
        if data.get('loyalty_type') and data['loyalty_type'] != organization.loyalty_type:
            raise ConflictError(
                code=ErrorCode.LOYALTY_TYPE_LOCKED,
                message='Тип программы выбирается при создании организации и не меняется',
            )

        update_loyalty_program(
            organization=organization,
            visit_data=data.get('visit_program'),
            cashback_data=data.get('cashback_program'),
            actor=request.user,
            request=request,
        )
        # Текст на карте хранится в организации (7.12 → 7.10)
        card = {key: data[key] for key in ('card_title', 'card_description') if key in data}
        if card:
            for key, value in card.items():
                setattr(organization, key, value)
            organization.save(update_fields=[*card.keys(), 'updated_at'])
        organization.refresh_from_db()
        return Response(program_payload(organization))


@extend_schema(tags=['admin'], request=OrganizationSelfSerializer, responses=OrganizationSelfSerializer)
class AdminOrganizationView(AdminBaseView):
    """«Моя организация» (7.10): читать — любой сотрудник, менять — администратор.

    slug, тип программы и статус здесь не меняются.
    """

    serializer_class = OrganizationSelfSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_required_permission(self, request):
        return ANY_STAFF if request.method == 'GET' else ROLE_ADMIN

    def get(self, request):
        return Response(OrganizationSelfSerializer(
            self.organization, context={'request': request},
        ).data)

    @transaction.atomic
    def patch(self, request):
        organization = self.organization
        tracked = ('name', 'phone', 'address', 'kind', 'instagram', 'website',
                   'card_title', 'card_description', 'showcase_title')
        before = {field: getattr(organization, field) for field in tracked}
        before.update({'category_id': str(organization.category_id or ''),
                       'has_logo': bool(organization.logo), 'has_cover': bool(organization.cover)})

        serializer = OrganizationSelfSerializer(
            organization, data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        after = {field: getattr(organization, field) for field in tracked}
        after.update({'category_id': str(organization.category_id or ''),
                      'has_logo': bool(organization.logo), 'has_cover': bool(organization.cover)})
        log_action(
            AuditAction.ORGANIZATION_UPDATED, actor=request.user, request=request,
            entity_type='Organization', entity_id=organization.id,
            organization=organization, before=before, after=after,
        )
        return Response(serializer.data)

"""DIRECTOR API: организации, доступы, пользователи, ledger, аудит (ТЗ §21)."""

import secrets

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import log_action
from apps.common.errors import ConflictError, DomainError, ErrorCode, NotFoundError
from apps.common.idempotency import run_idempotent
from apps.common.permissions import IsDirector
from apps.common.phone import normalize_phone
from apps.director.serializers import (
    AuditLogSerializer,
    BlockSerializer,
    CreateOrganizationAdminSerializer,
    DirectorUserSerializer,
    OrganizationCreateSerializer,
    OrganizationUpdateSerializer,
)
from apps.loyalty.models import Transaction, UserOrganizationState
from apps.loyalty.serializers import DirectorReverseSerializer, TransactionSerializer
from apps.loyalty.services import reversal as reversal_service
from apps.loyalty.services import statistics as statistics_service
from apps.organizations.models import (
    CashbackProgram,
    Category,
    LoyaltyType,
    Organization,
    OrganizationAdmin,
    OrganizationStatus,
    VisitProgram,
)
from apps.organizations.serializers import CategorySerializer, OrganizationDirectorSerializer
from apps.users.models import Role, User


class DirectorBaseView(APIView):
    permission_classes = [IsDirector]


def _unique_slug(name):
    base = slugify(name) or 'org'
    slug, index = base, 1
    while Organization.objects.filter(slug=slug).exists():
        index += 1
        slug = f'{base}-{index}'
    return slug


@extend_schema(tags=['director'], request=OrganizationCreateSerializer, responses=OrganizationDirectorSerializer)
class OrganizationListCreateView(DirectorBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(operation_id='director_organizations_list')
    def get(self, request):
        queryset = (
            Organization.objects.all()
            .select_related('category', 'visit_program', 'cashback_program')
            .annotate(customers_count=Count('user_states', distinct=True))
        )
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        loyalty_type = request.query_params.get('loyalty_type')
        if loyalty_type:
            queryset = queryset.filter(loyalty_type=loyalty_type)
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(phone__icontains=search))

        from apps.common.pagination import DefaultPagination
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = OrganizationDirectorSerializer(page, many=True, context={'request': request}).data
        return paginator.get_paginated_response(data)

    @transaction.atomic
    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        visit_data = data.pop('visit_program', None)
        cashback_data = data.pop('cashback_program', None)
        data.setdefault('slug', _unique_slug(data['name']))

        organization = Organization.objects.create(created_by=request.user, **data)

        if organization.loyalty_type == LoyaltyType.VISIT:
            VisitProgram.objects.create(organization=organization, **(visit_data or {}))
        else:
            CashbackProgram.objects.create(organization=organization, **(cashback_data or {}))

        log_action(
            AuditAction.ORGANIZATION_CREATED, actor=request.user, request=request,
            entity_type='Organization', entity_id=organization.id, organization=organization,
            after={'name': organization.name, 'loyalty_type': organization.loyalty_type},
        )
        return Response(
            OrganizationDirectorSerializer(organization, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['director'], request=OrganizationUpdateSerializer, responses=OrganizationDirectorSerializer)
class OrganizationDetailView(DirectorBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, pk):
        organization = (
            Organization.objects.filter(pk=pk)
            .select_related('category', 'visit_program', 'cashback_program')
            .first()
        )
        if organization is None:
            raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')
        return organization

    def get(self, request, pk):
        organization = self.get_object(pk)
        return Response(OrganizationDirectorSerializer(organization, context={'request': request}).data)

    @transaction.atomic
    def patch(self, request, pk):
        organization = self.get_object(pk)
        serializer = OrganizationUpdateSerializer(organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        visit_data = data.pop('visit_program', None)
        cashback_data = data.pop('cashback_program', None)
        new_type = data.get('loyalty_type')

        before = {
            'name': organization.name,
            'loyalty_type': organization.loyalty_type,
            'status': organization.status,
        }

        if new_type and new_type != organization.loyalty_type:
            # Смена типа программы после появления транзакций запрещена (§6).
            if Transaction.objects.filter(organization=organization).exists():
                raise ConflictError(
                    code=ErrorCode.LOYALTY_TYPE_LOCKED,
                    message='Нельзя сменить тип программы: по организации уже есть транзакции',
                )

        for field, value in data.items():
            setattr(organization, field, value)
        organization.save()

        if organization.loyalty_type == LoyaltyType.VISIT:
            program, _ = VisitProgram.objects.get_or_create(organization=organization)
            if visit_data:
                for field, value in visit_data.items():
                    setattr(program, field, value)
                program.save()
        else:
            program, _ = CashbackProgram.objects.get_or_create(organization=organization)
            if cashback_data:
                for field, value in cashback_data.items():
                    setattr(program, field, value)
                program.save()

        log_action(
            AuditAction.ORGANIZATION_UPDATED, actor=request.user, request=request,
            entity_type='Organization', entity_id=organization.id, organization=organization,
            before=before,
            after={'name': organization.name, 'loyalty_type': organization.loyalty_type},
        )
        if visit_data or cashback_data:
            log_action(
                AuditAction.PROGRAM_UPDATED, actor=request.user, request=request,
                entity_type='Organization', entity_id=organization.id, organization=organization,
                after=visit_data or cashback_data,
            )

        organization.refresh_from_db()
        return Response(OrganizationDirectorSerializer(organization, context={'request': request}).data)


@extend_schema(tags=['director'], request=BlockSerializer, responses=OpenApiTypes.OBJECT)
class OrganizationBlockView(DirectorBaseView):
    def post(self, request, pk):
        serializer = BlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = Organization.objects.filter(pk=pk).first()
        if organization is None:
            raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')

        organization.status = OrganizationStatus.BLOCKED
        organization.blocked_at = timezone.now()
        organization.blocked_reason = serializer.validated_data.get('reason', '')
        organization.save(update_fields=['status', 'blocked_at', 'blocked_reason', 'updated_at'])

        log_action(
            AuditAction.ORGANIZATION_BLOCKED, actor=request.user, request=request,
            entity_type='Organization', entity_id=organization.id, organization=organization,
            reason=organization.blocked_reason, after={'status': organization.status},
        )
        return Response({'id': str(organization.id), 'status': organization.status})


@extend_schema(tags=['director'], request=None, responses=OpenApiTypes.OBJECT)
class OrganizationUnblockView(DirectorBaseView):
    def post(self, request, pk):
        organization = Organization.objects.filter(pk=pk).first()
        if organization is None:
            raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')

        organization.status = OrganizationStatus.ACTIVE
        organization.blocked_at = None
        organization.blocked_reason = ''
        organization.save(update_fields=['status', 'blocked_at', 'blocked_reason', 'updated_at'])

        log_action(
            AuditAction.ORGANIZATION_UNBLOCKED, actor=request.user, request=request,
            entity_type='Organization', entity_id=organization.id, organization=organization,
            after={'status': organization.status},
        )
        return Response({'id': str(organization.id), 'status': organization.status})


@extend_schema(tags=['director'], request=CreateOrganizationAdminSerializer, responses=OpenApiTypes.OBJECT)
class OrganizationAdminView(DirectorBaseView):
    """Создание/сброс доступа администратора организации (один активный)."""

    @transaction.atomic
    def post(self, request, pk):
        serializer = CreateOrganizationAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        organization = Organization.objects.filter(pk=pk).first()
        if organization is None:
            raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')

        phone = data['phone']
        user = User.objects.filter(phone=phone).first()
        if user and user.role == Role.USER and user.is_registration_complete:
            raise ConflictError(
                code=ErrorCode.USER_ALREADY_EXISTS,
                message='Этот номер уже используется клиентом приложения',
            )
        if user and user.role == Role.DIRECTOR:
            raise ConflictError(
                code=ErrorCode.USER_ALREADY_EXISTS,
                message='Этот номер принадлежит директору',
            )

        active_membership = organization.admins.filter(is_active=True).first()
        if active_membership and not data.get('replace_existing'):
            raise ConflictError(
                code=ErrorCode.ADMIN_ALREADY_EXISTS,
                message='У организации уже есть активный администратор',
                details={'admin_id': str(active_membership.user_id)},
            )
        if active_membership:
            active_membership.is_active = False
            active_membership.deactivated_at = timezone.now()
            active_membership.save(update_fields=['is_active', 'deactivated_at', 'updated_at'])

        if user is None:
            user = User(phone=phone, role=Role.ORGANIZATION_ADMIN)
            user.set_unusable_password()
        user.role = Role.ORGANIZATION_ADMIN
        user.first_name = data.get('first_name', '') or user.first_name
        user.last_name = data.get('last_name', '') or user.last_name
        user.is_active = True
        user.is_registration_complete = True

        # Временный PIN выдаётся один раз и показывается директору.
        temp_pin = data.get('pin') or f'{secrets.randbelow(9000) + 1000}'
        user.set_pin(temp_pin)
        user.save()

        OrganizationAdmin.objects.filter(user=user, is_active=True).update(
            is_active=False, deactivated_at=timezone.now()
        )
        membership = OrganizationAdmin.objects.create(
            organization=organization, user=user, created_by=request.user, is_active=True
        )

        log_action(
            AuditAction.ADMIN_CREATED if not active_membership else AuditAction.ADMIN_RESET,
            actor=request.user, request=request,
            entity_type='OrganizationAdmin', entity_id=membership.id, organization=organization,
            after={'user_id': str(user.id), 'phone': user.phone},
        )

        return Response({
            'admin': {
                'id': str(user.id),
                'phone': user.phone,
                'full_name': user.full_name,
                'organization_id': str(organization.id),
            },
            # Единственный момент, когда PIN виден: дальше только хеш.
            'temporary_pin': temp_pin,
        }, status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        membership = OrganizationAdmin.objects.filter(organization_id=pk, is_active=True).first()
        if membership is None:
            raise NotFoundError(message='Активный администратор не найден')
        membership.is_active = False
        membership.deactivated_at = timezone.now()
        membership.save(update_fields=['is_active', 'deactivated_at', 'updated_at'])

        log_action(
            AuditAction.ADMIN_RESET, actor=request.user, request=request,
            entity_type='OrganizationAdmin', entity_id=membership.id,
            organization=membership.organization, after={'is_active': False},
        )
        return Response({'detail': 'Доступ администратора отозван'})


@extend_schema(tags=['director'])
class DirectorUsersView(DirectorBaseView, ListAPIView):
    """Поиск клиентов по телефону, имени, ID и 6-значному коду."""

    serializer_class = DirectorUserSerializer

    def get_queryset(self):
        queryset = User.objects.filter(role=Role.USER)
        search = self.request.query_params.get('search', '').strip()
        if search:
            filters = Q(first_name__icontains=search) | Q(last_name__icontains=search)
            if search.isdigit() and len(search) == 6:
                filters |= Q(public_code=search)
            if search.startswith('+') or search.isdigit():
                filters |= Q(phone__icontains=search)
            try:
                import uuid as _uuid
                filters |= Q(id=_uuid.UUID(search))
            except (ValueError, AttributeError):
                pass
            queryset = queryset.filter(filters)
        return queryset.order_by('-date_joined')


@extend_schema(tags=['director'], responses=OpenApiTypes.OBJECT)
class DirectorUserDetailView(DirectorBaseView):
    def get(self, request, pk):
        user = User.objects.filter(pk=pk, role=Role.USER).first()
        if user is None:
            raise NotFoundError(code=ErrorCode.USER_NOT_FOUND, message='Пользователь не найден')

        states = (
            UserOrganizationState.objects.filter(user=user)
            .select_related('organization')
            .order_by('-last_activity_at')
        )
        return Response({
            **DirectorUserSerializer(user).data,
            'organizations': [
                {
                    'organization_id': str(state.organization_id),
                    'organization_name': state.organization.name,
                    'loyalty_type': state.organization.loyalty_type,
                    'visit_progress': state.visit_progress,
                    'available_gifts': state.available_gifts,
                    'cashback_available': state.cashback_available,
                    'last_activity_at': state.last_activity_at,
                }
                for state in states
            ],
        })


@extend_schema(tags=['director'])
class DirectorTransactionsView(DirectorBaseView, ListAPIView):
    """Глобальный журнал транзакций платформы."""

    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = Transaction.objects.all().select_related('organization')
        params = self.request.query_params
        if params.get('organization_id'):
            queryset = queryset.filter(organization_id=params['organization_id'])
        if params.get('user_id'):
            queryset = queryset.filter(user_id=params['user_id'])
        if params.get('type'):
            queryset = queryset.filter(type=params['type'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('date_from'):
            queryset = queryset.filter(created_at__gte=params['date_from'])
        if params.get('date_to'):
            queryset = queryset.filter(created_at__lte=params['date_to'])
        return queryset


@extend_schema(tags=['director'], request=DirectorReverseSerializer, responses=OpenApiTypes.OBJECT)
class DirectorReverseView(DirectorBaseView):
    """Отмена любой операции платформы, в т.ч. эскалированных случаев."""

    def post(self, request, pk):
        serializer = DirectorReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def handler():
            result = reversal_service.reverse_transaction(
                transaction_id=pk, actor=request.user,
                reason=serializer.validated_data['reason'],
                organization=None, force=serializer.validated_data.get('force', False),
                request=request,
            )
            return {
                'original_transaction_id': str(result['original'].id),
                'reversal_transaction_id': str(result['reversal'].id),
                'status': result['original'].status,
            }

        body, status_code = run_idempotent(
            request, f'director/transactions/{pk}/reverse', request.data, handler, required=False
        )
        return Response(body, status=status_code)


@extend_schema(tags=['director'], responses=OpenApiTypes.OBJECT)
class DirectorStatisticsView(DirectorBaseView):
    def get(self, request):
        organization_id = request.query_params.get('organization_id')
        if organization_id:
            organization = Organization.objects.filter(pk=organization_id).first()
            if organization is None:
                raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')
            return Response(statistics_service.organization_statistics(organization))
        return Response(statistics_service.platform_statistics())


@extend_schema(tags=['director'])
class DirectorAuditView(DirectorBaseView, ListAPIView):
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        queryset = AuditLog.objects.all().select_related('organization')
        params = self.request.query_params
        if params.get('action'):
            queryset = queryset.filter(action=params['action'])
        if params.get('actor_id'):
            queryset = queryset.filter(actor_id=params['actor_id'])
        if params.get('organization_id'):
            queryset = queryset.filter(organization_id=params['organization_id'])
        if params.get('entity_id'):
            queryset = queryset.filter(entity_id=params['entity_id'])
        if params.get('date_from'):
            queryset = queryset.filter(created_at__gte=params['date_from'])
        if params.get('date_to'):
            queryset = queryset.filter(created_at__lte=params['date_to'])
        return queryset


@extend_schema(tags=['director'], request=CategorySerializer, responses=CategorySerializer)
class DirectorCategoriesView(DirectorBaseView):
    def get(self, request):
        return Response(CategorySerializer(Category.objects.all(), many=True).data)

    def post(self, request):
        serializer = CategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.save()
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['director'], responses=OpenApiTypes.OBJECT)
class DirectorDashboardView(DirectorBaseView):
    def get(self, request):
        stats = statistics_service.platform_statistics()
        recent = (
            Organization.objects.all().order_by('-created_at')[:5]
        )
        return Response({
            'statistics': stats,
            'recent_organizations': OrganizationDirectorSerializer(
                recent, many=True, context={'request': request}
            ).data,
        })

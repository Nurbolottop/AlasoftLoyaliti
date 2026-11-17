"""Каталог заведений, витрина, «Присоединиться» и избранное (BACKEND_TZ 6.3–6.6).

Каталог и витрина открыты гостю: без токена is_member и is_favorite — false.
"""

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import DomainError, ErrorCode, NotFoundError
from apps.common.permissions import IsUser
from apps.common.request_context import request_language
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_organization_admins
from apps.organizations.hours import distance_km, is_open_now
from apps.organizations.models import (
    CatalogItem,
    CatalogSection,
    Category,
    FavoriteItem,
    FavoriteOrganization,
    Organization,
    OrganizationStatus,
)
from apps.organizations.serializers import (
    CatalogItemSerializer,
    CategorySerializer,
    OrganizationPublicSerializer,
)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _customer(request):
    """Покупатель или None (гость, директор)."""
    user = getattr(request, 'user', None)
    if not (user and getattr(user, 'is_authenticated', False)) or user.role == 'DIRECTOR':
        return None
    return user


def organization_context(request, organizations=None):
    """Контекст карточки: участие и избранное покупателя, его координаты."""
    from apps.loyalty.models import UserOrganizationState

    context = {
        'request': request,
        'language': request_language(request),
        'lat': _float(request.query_params.get('lat')),
        'lng': _float(request.query_params.get('lng')),
        'member_ids': set(),
        'favorite_ids': set(),
    }
    user = _customer(request)
    if user is not None:
        ids = [o.id for o in organizations] if organizations is not None else None
        members = UserOrganizationState.objects.filter(user=user, joined_at__isnull=False)
        favorites = FavoriteOrganization.objects.filter(user=user)
        if ids is not None:
            members = members.filter(organization_id__in=ids)
            favorites = favorites.filter(organization_id__in=ids)
        context['member_ids'] = set(members.values_list('organization_id', flat=True))
        context['favorite_ids'] = set(favorites.values_list('organization_id', flat=True))
    return context


def active_organizations():
    return Organization.objects.filter(status=OrganizationStatus.ACTIVE).select_related(
        'category', 'visit_program', 'cashback_program',
    )


def get_active_organization(pk):
    organization = active_organizations().filter(pk=pk).first()
    if organization is None:
        raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')
    return organization


@extend_schema(tags=['user'], parameters=[
    OpenApiParameter('search', str, description='Название или адрес'),
    OpenApiParameter('category', str, description='id категории'),
    OpenApiParameter('loyalty_type', str, enum=['VISIT', 'CASHBACK']),
    OpenApiParameter('lat', float), OpenApiParameter('lng', float),
    OpenApiParameter('ordering', str, enum=['distance', 'name', 'rating']),
    OpenApiParameter('open_now', bool), OpenApiParameter('favorites', bool),
    OpenApiParameter('tag', str, description='Метка (чипы под поиском)'),
])
class OrganizationCatalogView(ListAPIView):
    """Каталог активных заведений (6.4). Элемент — 3.2 без программы."""

    serializer_class = OrganizationPublicSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        params = self.request.query_params
        queryset = active_organizations()
        search = params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(address__icontains=search) | Q(kind__icontains=search)
            )
        if params.get('category'):
            queryset = queryset.filter(category_id=params['category'])
        if params.get('loyalty_type') in ('VISIT', 'CASHBACK'):
            queryset = queryset.filter(loyalty_type=params['loyalty_type'])
        if params.get('favorites') in ('true', '1'):
            user = _customer(self.request)
            queryset = queryset.filter(favorited_by__user=user) if user else queryset.none()
        tag = params.get('tag', '').strip()
        if tag:
            queryset = queryset.filter(tags__icontains=tag)
        return queryset.order_by('name')

    def list(self, request, *args, **kwargs):
        params = request.query_params
        organizations = list(self.get_queryset())
        tag = params.get('tag', '').strip().lower()
        if tag:
            # icontains по JSON ловит и подстроки — оставляем точное совпадение метки
            organizations = [o for o in organizations if tag in [t.lower() for t in (o.tags or [])]]
        if params.get('open_now') in ('true', '1'):
            organizations = [o for o in organizations if is_open_now(o.working_hours)]

        lat, lng = _float(params.get('lat')), _float(params.get('lng'))
        if params.get('ordering') == 'distance' and lat is not None and lng is not None:
            far = float('inf')
            organizations.sort(key=lambda o: distance_km(lat, lng, o.latitude, o.longitude) or far)

        page = self.paginate_queryset(organizations)
        # Программу отдаём и в списке: старые версии берут из неё подпись условий
        context = organization_context(request, page)
        return self.get_paginated_response(OrganizationPublicSerializer(page, many=True, context=context).data)


@extend_schema(tags=['user'], responses=OrganizationPublicSerializer)
class OrganizationDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        organization = get_active_organization(pk)
        return Response(OrganizationPublicSerializer(
            organization, context=organization_context(request, [organization]),
        ).data)


@extend_schema(tags=['user'], request=None, responses=OrganizationPublicSerializer)
class OrganizationJoinView(APIView):
    """«Присоединиться»: заведение в «Мои карты», покупатель — в клиентах заведения (6.5)."""

    permission_classes = [IsUser]

    @transaction.atomic
    def post(self, request, pk):
        from apps.loyalty.models import UserOrganizationState

        organization = Organization.objects.filter(pk=pk).select_related(
            'category', 'visit_program', 'cashback_program').first()
        if organization is None:
            raise NotFoundError(code=ErrorCode.ORGANIZATION_NOT_FOUND, message='Организация не найдена')
        if organization.status != OrganizationStatus.ACTIVE:
            raise DomainError(code=ErrorCode.ORGANIZATION_BLOCKED, message='Организация заблокирована',
                              status_code=409)

        user = request.user
        user.ensure_identity()
        state, _ = UserOrganizationState.objects.select_for_update().get_or_create(
            user=user, organization=organization,
        )
        if state.joined_at is None:
            state.joined_at = timezone.now()
            state.save(update_fields=['joined_at', 'updated_at'])
            log_action(AuditAction.ORGANIZATION_JOINED, actor=user, request=request,
                       entity_type='Organization', entity_id=organization.id, organization=organization)
            notify_organization_admins(organization, NotificationEvent.CUSTOMER_JOINED, context={
                'customer_id': str(user.id), 'customer_name': user.full_name or user.phone,
            })

        return Response(OrganizationPublicSerializer(
            organization, context=organization_context(request, [organization]),
        ).data)


class FavoriteOrganizationView(APIView):
    permission_classes = [IsUser]

    @extend_schema(tags=['user'], request=None, responses={204: None})
    def put(self, request, pk):
        organization = get_active_organization(pk)
        FavoriteOrganization.objects.get_or_create(user=request.user, organization=organization)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=['user'], request=None, responses={204: None})
    def delete(self, request, pk):
        FavoriteOrganization.objects.filter(user=request.user, organization_id=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FavoriteItemView(APIView):
    permission_classes = [IsUser]

    @extend_schema(tags=['user'], request=None, responses={204: None})
    def put(self, request, pk):
        item = CatalogItem.objects.filter(pk=pk, organization__status=OrganizationStatus.ACTIVE).first()
        if item is None:
            raise NotFoundError(code=ErrorCode.CATALOG_ITEM_NOT_FOUND, message='Позиция не найдена')
        FavoriteItem.objects.get_or_create(user=request.user, item=item)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=['user'], request=None, responses={204: None})
    def delete(self, request, pk):
        FavoriteItem.objects.filter(user=request.user, item_id=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _favorite_item_ids(request):
    user = _customer(request)
    if user is None:
        return set()
    return set(FavoriteItem.objects.filter(user=user).values_list('item_id', flat=True))


@extend_schema(tags=['user'], responses=OpenApiTypes.OBJECT)
class OrganizationCatalogItemsView(APIView):
    """Витрина заведения (6.6): разделы с позициями, пустые не отдаются."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        organization = get_active_organization(pk)
        context = {'request': request, 'favorite_ids': _favorite_item_ids(request)}
        sections = (
            CatalogSection.objects.filter(organization=organization)
            .prefetch_related('items').order_by('sort_order', 'created_at')
        )
        return Response({
            'showcase_title': organization.showcase_title,
            'sections': [
                {
                    'id': str(section.id),
                    'name': section.name,
                    'description': section.description,
                    'items': CatalogItemSerializer(
                        sorted(section.items.all(), key=lambda i: (i.sort_order, i.created_at)),
                        many=True, context=context,
                    ).data,
                }
                for section in sections if section.items.all()
            ],
        })


@extend_schema(tags=['user'], responses=CatalogItemSerializer)
class OrganizationCatalogItemView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk, item_id):
        organization = get_active_organization(pk)
        item = CatalogItem.objects.filter(organization=organization, pk=item_id).first()
        if item is None:
            raise NotFoundError(code=ErrorCode.CATALOG_ITEM_NOT_FOUND, message='Позиция не найдена')
        return Response(CatalogItemSerializer(
            item, context={'request': request, 'favorite_ids': _favorite_item_ids(request)},
        ).data)


@extend_schema(tags=['user'], responses=CategorySerializer(many=True))
class CategoryListView(APIView):
    """Плитки категорий (6.3). Доступно и гостю, и в форме «Тип бизнеса»."""

    permission_classes = [AllowAny]
    serializer_class = CategorySerializer

    def get(self, request):
        # Счётчик считаем по тем же организациям, что видны в каталоге:
        # активным и не заблокированным — иначе фильтр ведёт на пустой экран.
        categories = Category.objects.filter(is_active=True).annotate(
            organizations_count=Count(
                'organizations',
                filter=Q(organizations__status=OrganizationStatus.ACTIVE),
                distinct=True,
            )
        )
        if request.query_params.get('has_organizations') in ('true', '1'):
            categories = categories.filter(organizations_count__gt=0)

        return Response(
            CategorySerializer(categories, many=True, context={'language': request_language(request)}).data
        )

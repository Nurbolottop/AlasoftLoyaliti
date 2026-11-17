"""Сотрудники и каталог заведения в TOPTO Business (BACKEND_TZ 7.11, 7.13)."""

from django.db import transaction
from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import ErrorCode, NotFoundError
from apps.common.permissions import ANY_STAFF, ROLE_ADMIN, _denied
from apps.loyalty.services import business as business_service
from apps.loyalty.views_admin import PERIOD_PARAM, AdminBaseView
from apps.organizations import employees as employee_service
from apps.organizations.models import CatalogItem, CatalogSection
from apps.organizations.serializers import (
    CatalogItemSerializer,
    CatalogSectionSerializer,
    EmployeeSerializer,
    EmployeeWriteSerializer,
    ServiceBriefSerializer,
)


# --- сотрудники ---------------------------------------------------------------------

@extend_schema(tags=['admin-staff'], request=EmployeeWriteSerializer, responses=EmployeeSerializer(many=True))
class EmployeeListView(AdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        employees = (
            employee_service.live_employees(self.organization)
            .select_related('user').order_by('position', 'created_at')
        )
        return Response(EmployeeSerializer(employees, many=True, context={'request': request}).data)

    def post(self, request):
        serializer = EmployeeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = employee_service.create_employee(
            organization=self.organization, data=serializer.validated_data,
            actor=request.user, request=request,
        )
        return Response(EmployeeSerializer(employee, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=['admin-staff'], request=EmployeeWriteSerializer, responses=EmployeeSerializer)
class EmployeeDetailView(AdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, pk):
        employee = employee_service.get_employee(self.organization, pk)
        return Response(EmployeeSerializer(employee, context={'request': request}).data)

    def patch(self, request, pk):
        employee = employee_service.get_employee(self.organization, pk)
        serializer = EmployeeWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        employee = employee_service.update_employee(
            employee=employee, data=serializer.validated_data, actor=request.user, request=request,
        )
        return Response(EmployeeSerializer(employee, context={'request': request}).data)

    def delete(self, request, pk):
        employee = employee_service.get_employee(self.organization, pk)
        employee_service.delete_employee(employee=employee, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['admin-staff'], parameters=[PERIOD_PARAM], responses=OpenApiTypes.OBJECT)
class EmployeeStatsView(AdminBaseView):
    """Результаты работы. Сотрудник видит только свои, администратор — всех."""

    required_permission = ANY_STAFF

    def get(self, request, pk):
        employee = employee_service.get_employee(self.organization, pk)
        if not self.is_admin and employee.pk != self.staff.pk:
            raise _denied(ROLE_ADMIN, 'Можно смотреть только свои результаты')
        period = business_service.parse_period(request.query_params.get('period'), default=1)
        return Response(business_service.employee_stats(employee, period))


# --- каталог -----------------------------------------------------------------------

def _log_catalog(request, organization, entity, entity_id, after=None, before=None):
    log_action(AuditAction.CATALOG_UPDATED, actor=request.user, request=request,
               entity_type=entity, entity_id=entity_id, organization=organization,
               before=before or {}, after=after or {})


class CatalogMixin:
    def sections(self):
        return CatalogSection.objects.filter(organization=self.organization)

    def get_section(self, pk):
        section = self.sections().filter(pk=pk).first()
        if section is None:
            raise NotFoundError(code=ErrorCode.CATALOG_SECTION_NOT_FOUND, message='Раздел не найден')
        return section

    def get_item(self, pk):
        item = CatalogItem.objects.filter(organization=self.organization, pk=pk).first()
        if item is None:
            raise NotFoundError(code=ErrorCode.CATALOG_ITEM_NOT_FOUND, message='Позиция не найдена')
        return item


@extend_schema(tags=['admin-catalog'], request=CatalogSectionSerializer,
               responses=CatalogSectionSerializer(many=True))
class CatalogSectionListView(CatalogMixin, AdminBaseView):
    def get(self, request):
        sections = self.sections().annotate(items_count=Count('items'))
        return Response(CatalogSectionSerializer(sections, many=True).data)

    def post(self, request):
        serializer = CatalogSectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = serializer.save(organization=self.organization)
        section.items_count = 0
        _log_catalog(request, self.organization, 'CatalogSection', section.id, after={'name': section.name})
        return Response(CatalogSectionSerializer(section).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['admin-catalog'], request=CatalogSectionSerializer, responses=CatalogSectionSerializer)
class CatalogSectionDetailView(CatalogMixin, AdminBaseView):
    def patch(self, request, pk):
        section = self.get_section(pk)
        serializer = CatalogSectionSerializer(section, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        section = serializer.save()
        section.items_count = section.items.count()
        _log_catalog(request, self.organization, 'CatalogSection', section.id, after={'name': section.name})
        return Response(CatalogSectionSerializer(section).data)

    @transaction.atomic
    def delete(self, request, pk):
        """Удаляет раздел вместе с позициями. Прошлые операции не меняются (3.4)."""
        section = self.get_section(pk)
        for item in section.items.all():
            if item.image:
                item.image.delete(save=False)
        _log_catalog(request, self.organization, 'CatalogSection', section.id, before={'name': section.name})
        section.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['admin-catalog'], request=CatalogItemSerializer, responses=CatalogItemSerializer(many=True))
class CatalogSectionItemsView(CatalogMixin, AdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, pk):
        section = self.get_section(pk)
        return Response(CatalogItemSerializer(section.items.all(), many=True, context={'request': request}).data)

    def post(self, request, pk):
        section = self.get_section(pk)
        serializer = CatalogItemSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        item = serializer.save(section=section, organization=self.organization)
        _log_catalog(request, self.organization, 'CatalogItem', item.id,
                     after={'name': item.name, 'price': item.price})
        return Response(CatalogItemSerializer(item, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=['admin-catalog'], request=CatalogItemSerializer, responses=CatalogItemSerializer)
class CatalogItemDetailView(CatalogMixin, AdminBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, pk):
        return Response(CatalogItemSerializer(self.get_item(pk), context={'request': request}).data)

    def patch(self, request, pk):
        item = self.get_item(pk)
        before = {'name': item.name, 'price': item.price}
        data = request.data
        # Перенос в другой раздел — только внутри своего каталога
        section_id = data.get('section_id')
        serializer = CatalogItemSerializer(item, data=data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        extra = {'section': self.get_section(section_id)} if section_id else {}
        item = serializer.save(**extra)
        _log_catalog(request, self.organization, 'CatalogItem', item.id, before=before,
                     after={'name': item.name, 'price': item.price})
        return Response(CatalogItemSerializer(item, context={'request': request}).data)

    def delete(self, request, pk):
        item = self.get_item(pk)
        if item.image:
            item.image.delete(save=False)
        _log_catalog(request, self.organization, 'CatalogItem', item.id,
                     before={'name': item.name, 'price': item.price})
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['admin-catalog'], responses=ServiceBriefSerializer(many=True),
               parameters=[OpenApiParameter('search', str)])
class CatalogServicesView(CatalogMixin, AdminBaseView):
    """Все позиции одним списком — для «Начислить бонус» (7.5)."""

    required_permission = ANY_STAFF

    def get(self, request):
        items = (
            CatalogItem.objects.filter(organization=self.organization)
            .order_by('section__sort_order', 'section__created_at', 'sort_order', 'created_at')
        )
        search = request.query_params.get('search', '').strip()
        if search:
            items = items.filter(name__icontains=search)
        return Response(ServiceBriefSerializer(items, many=True).data)

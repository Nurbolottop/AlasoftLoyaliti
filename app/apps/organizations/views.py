from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Category, Organization, OrganizationStatus
from apps.organizations.serializers import CategorySerializer, OrganizationPublicSerializer


class LanguageContextMixin:
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['language'] = getattr(self.request.user, 'language', 'ru')
        return context


@extend_schema(tags=['user'])
class OrganizationCatalogView(LanguageContextMixin, ListAPIView):
    """Каталог активных организаций (ТЗ общее §9)."""

    serializer_class = OrganizationPublicSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['loyalty_type', 'category']

    def get_queryset(self):
        queryset = (
            Organization.objects.filter(status=OrganizationStatus.ACTIVE)
            .select_related('category', 'visit_program', 'cashback_program')
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(address__icontains=search)
            )
        return queryset


@extend_schema(tags=['user'])
class OrganizationDetailView(LanguageContextMixin, RetrieveAPIView):
    serializer_class = OrganizationPublicSerializer
    permission_classes = [IsAuthenticated]
    queryset = Organization.objects.filter(status=OrganizationStatus.ACTIVE).select_related(
        'category', 'visit_program', 'cashback_program'
    )


@extend_schema(tags=['user'], responses=CategorySerializer(many=True))
class CategoryListView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CategorySerializer

    def get(self, request):
        categories = Category.objects.filter(is_active=True)
        language = getattr(request.user, 'language', 'ru')
        return Response(
            CategorySerializer(categories, many=True, context={'language': language}).data
        )

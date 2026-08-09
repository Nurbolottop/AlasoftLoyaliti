from django.urls import path

from apps.organizations import views

urlpatterns = [
    path('organizations', views.OrganizationCatalogView.as_view(), name='organization-catalog'),
    path('organizations/<uuid:pk>', views.OrganizationDetailView.as_view(), name='organization-detail'),
    path('categories', views.CategoryListView.as_view(), name='category-list'),
]

from django.urls import path

from apps.organizations import views

urlpatterns = [
    path('organizations', views.OrganizationCatalogView.as_view(), name='organization-catalog'),
    path('organizations/<uuid:pk>', views.OrganizationDetailView.as_view(), name='organization-detail'),
    path('organizations/<uuid:pk>/join', views.OrganizationJoinView.as_view(), name='organization-join'),
    path('organizations/<uuid:pk>/catalog', views.OrganizationCatalogItemsView.as_view(),
         name='organization-catalog-items'),
    path('organizations/<uuid:pk>/catalog/items/<uuid:item_id>', views.OrganizationCatalogItemView.as_view(),
         name='organization-catalog-item'),
    path('me/favorites/organizations/<uuid:pk>', views.FavoriteOrganizationView.as_view(),
         name='favorite-organization'),
    path('me/favorites/items/<uuid:pk>', views.FavoriteItemView.as_view(), name='favorite-item'),
    path('categories', views.CategoryListView.as_view(), name='category-list'),
]

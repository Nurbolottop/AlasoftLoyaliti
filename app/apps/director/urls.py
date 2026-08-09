from django.urls import path

from apps.director import views

urlpatterns = [
    path('dashboard', views.DirectorDashboardView.as_view(), name='director-dashboard'),
    path('organizations', views.OrganizationListCreateView.as_view(), name='director-organizations'),
    path('organizations/<uuid:pk>', views.OrganizationDetailView.as_view(), name='director-organization'),
    path('organizations/<uuid:pk>/block', views.OrganizationBlockView.as_view(), name='director-organization-block'),
    path('organizations/<uuid:pk>/unblock', views.OrganizationUnblockView.as_view(), name='director-organization-unblock'),
    path('organizations/<uuid:pk>/admin', views.OrganizationAdminView.as_view(), name='director-organization-admin'),
    path('categories', views.DirectorCategoriesView.as_view(), name='director-categories'),
    path('users', views.DirectorUsersView.as_view(), name='director-users'),
    path('users/<uuid:pk>', views.DirectorUserDetailView.as_view(), name='director-user'),
    path('transactions', views.DirectorTransactionsView.as_view(), name='director-transactions'),
    path('transactions/<uuid:pk>/reverse', views.DirectorReverseView.as_view(), name='director-transaction-reverse'),
    path('statistics', views.DirectorStatisticsView.as_view(), name='director-statistics'),
    path('audit', views.DirectorAuditView.as_view(), name='director-audit'),
]

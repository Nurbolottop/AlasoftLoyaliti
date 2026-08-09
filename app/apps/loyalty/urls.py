from django.urls import path

from apps.loyalty import views_admin, views_user

user_urlpatterns = [
    path('home', views_user.HomeView.as_view(), name='user-home'),
    path('me/loyalty', views_user.MyLoyaltyView.as_view(), name='me-loyalty'),
    path('me/transactions', views_user.MyTransactionsView.as_view(), name='me-transactions'),
    path('me/gifts', views_user.MyGiftsView.as_view(), name='me-gifts'),
    path('me/cashback', views_user.MyCashbackView.as_view(), name='me-cashback'),
    path('me/redemptions/pending', views_user.MyPendingRedemptionsView.as_view(), name='me-redemptions-pending'),
    path('me/redemptions/<uuid:pk>/confirm', views_user.ConfirmRedemptionView.as_view(), name='me-redemption-confirm'),
    path('me/redemptions/<uuid:pk>/reject', views_user.RejectRedemptionView.as_view(), name='me-redemption-reject'),
]

admin_urlpatterns = [
    path('dashboard', views_admin.AdminDashboardView.as_view(), name='admin-dashboard'),
    path('customers/resolve-qr', views_admin.ResolveQrView.as_view(), name='admin-resolve-qr'),
    path('customers/resolve-code', views_admin.ResolveCodeView.as_view(), name='admin-resolve-code'),
    path('customers', views_admin.AdminCustomersView.as_view(), name='admin-customers'),
    path('customers/<uuid:pk>/state', views_admin.CustomerStateView.as_view(), name='admin-customer-state'),
    path('visits', views_admin.VisitEarnView.as_view(), name='admin-visits'),
    path('gifts/<uuid:pk>/redeem-request', views_admin.GiftRedeemRequestView.as_view(), name='admin-gift-redeem'),
    path('cashback/quote', views_admin.CashbackQuoteView.as_view(), name='admin-cashback-quote'),
    path('cashback/earn', views_admin.CashbackEarnView.as_view(), name='admin-cashback-earn'),
    path('cashback/redeem-request', views_admin.CashbackRedeemRequestView.as_view(), name='admin-cashback-redeem'),
    path('redemptions', views_admin.AdminRedemptionsView.as_view(), name='admin-redemptions'),
    path('redemptions/<uuid:pk>/cancel', views_admin.AdminRedemptionCancelView.as_view(), name='admin-redemption-cancel'),
    path('transactions', views_admin.AdminTransactionsView.as_view(), name='admin-transactions'),
    path('transactions/<uuid:pk>/reverse', views_admin.AdminReverseView.as_view(), name='admin-transaction-reverse'),
    path('statistics', views_admin.AdminStatisticsView.as_view(), name='admin-statistics'),
]

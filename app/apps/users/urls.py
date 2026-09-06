from django.urls import path

from apps.users import views

auth_urlpatterns = [
    path('otp/request', views.OtpRequestView.as_view(), name='otp-request'),
    path('otp/verify', views.OtpVerifyView.as_view(), name='otp-verify'),
    path('register/complete', views.RegisterCompleteView.as_view(), name='register-complete'),
    path('otp/login', views.OtpLoginView.as_view(), name='otp-login'),
    path('pin/login', views.PinLoginView.as_view(), name='pin-login'),
    path('pin/reset', views.PinResetView.as_view(), name='pin-reset'),
    path('director/login', views.DirectorLoginView.as_view(), name='director-login'),
    path('refresh', views.TokenRefreshView.as_view(), name='token-refresh'),
    path('logout', views.LogoutView.as_view(), name='logout'),
]

me_urlpatterns = [
    path('me', views.MeView.as_view(), name='me'),
    path('me/devices', views.MeDevicesView.as_view(), name='me-devices'),
    path('me/qr', views.MeQrView.as_view(), name='me-qr'),
    path('me/avatar', views.MeAvatarView.as_view(), name='me-avatar'),
    path('me/phone/request', views.MePhoneChangeRequestView.as_view(), name='me-phone-request'),
    path('me/phone/confirm', views.MePhoneChangeConfirmView.as_view(), name='me-phone-confirm'),
    path('me/delete/request', views.MeDeleteRequestView.as_view(), name='me-delete-request'),
    path('me/delete/confirm', views.MeDeleteConfirmView.as_view(), name='me-delete-confirm'),
]

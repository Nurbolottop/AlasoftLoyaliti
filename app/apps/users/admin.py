from django.contrib import admin

from apps.users.models import OtpChallenge, User, UserDevice


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['phone', 'full_name', 'role', 'public_code', 'is_active', 'date_joined']
    list_filter = ['role', 'is_active', 'is_registration_complete', 'language']
    search_fields = ['phone', 'first_name', 'last_name', 'public_code']
    readonly_fields = ['id', 'pin_hash', 'qr_token', 'public_code', 'date_joined', 'last_login_at']
    ordering = ['-date_joined']


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_id', 'platform', 'is_active', 'last_seen_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['user__phone', 'device_id']
    readonly_fields = ['refresh_jti']


@admin.register(OtpChallenge)
class OtpChallengeAdmin(admin.ModelAdmin):
    list_display = ['phone', 'purpose', 'status', 'attempts', 'expires_at', 'created_at']
    list_filter = ['purpose', 'status']
    search_fields = ['phone']
    readonly_fields = ['code_hash', 'verification_token']

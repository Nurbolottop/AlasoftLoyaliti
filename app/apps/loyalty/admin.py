from django.contrib import admin

from apps.loyalty.models import (
    CashbackLot,
    Gift,
    RedemptionRequest,
    Transaction,
    UserOrganizationState,
)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Ledger доступен только на чтение: записи не правятся и не удаляются."""

    list_display = ['created_at', 'type', 'status', 'amount', 'organization', 'user']
    list_filter = ['type', 'status', 'organization']
    search_fields = ['user__phone', 'user__public_code', 'id']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserOrganizationState)
class UserOrganizationStateAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'visit_progress', 'available_gifts',
                    'cashback_available', 'last_activity_at']
    list_filter = ['organization']
    search_fields = ['user__phone', 'user__public_code']


@admin.register(Gift)
class GiftAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'organization', 'status', 'created_at', 'used_at']
    list_filter = ['status', 'organization']
    search_fields = ['user__phone', 'user__public_code']


@admin.register(CashbackLot)
class CashbackLotAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'organization', 'original_amount', 'remaining_amount',
                    'status', 'expires_at']
    list_filter = ['status', 'organization']
    search_fields = ['user__phone', 'user__public_code']
    date_hierarchy = 'expires_at'


@admin.register(RedemptionRequest)
class RedemptionRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'type', 'status', 'user', 'organization', 'spend_amount', 'created_at']
    list_filter = ['type', 'status', 'organization']
    search_fields = ['user__phone', 'user__public_code']

from django.contrib import admin

from apps.organizations.models import (
    CashbackProgram,
    Category,
    Organization,
    OrganizationAdmin as OrganizationAdminModel,
    VisitProgram,
)


class VisitProgramInline(admin.StackedInline):
    model = VisitProgram
    extra = 0


class CashbackProgramInline(admin.StackedInline):
    model = CashbackProgram
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'loyalty_type', 'status', 'category', 'created_at']
    list_filter = ['loyalty_type', 'status', 'category']
    search_fields = ['name', 'phone', 'address']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [VisitProgramInline, CashbackProgramInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name_ru', 'name_ky', 'slug', 'sort_order', 'is_active']
    prepopulated_fields = {'slug': ('name_ru',)}


@admin.register(OrganizationAdminModel)
class OrganizationAdminAdmin(admin.ModelAdmin):
    list_display = ['organization', 'user', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['organization__name', 'user__phone']

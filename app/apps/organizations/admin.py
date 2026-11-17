from django.contrib import admin

from apps.organizations.models import (
    CashbackProgram,
    CatalogItem,
    CatalogSection,
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
    list_display = ['organization', 'user', 'full_name', 'position', 'is_active', 'deleted_at', 'created_at']
    list_filter = ['position', 'is_active']
    search_fields = ['organization__name', 'user__phone', 'full_name']


class CatalogItemInline(admin.TabularInline):
    model = CatalogItem
    extra = 0
    fields = ['name', 'price', 'short_description', 'sort_order']


@admin.register(CatalogSection)
class CatalogSectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'organization', 'sort_order']
    search_fields = ['name', 'organization__name']
    inlines = [CatalogItemInline]

    def save_formset(self, request, form, formset, change):
        for item in formset.save(commit=False):
            item.organization = form.instance.organization
            item.save()
        formset.save_m2m()
        for item in formset.deleted_objects:
            item.delete()

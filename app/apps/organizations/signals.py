"""Гарантия целостности организации."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.organizations.models import Organization


@receiver(post_save, sender=Organization, dispatch_uid='organization_ensure_program')
def create_program_for_new_organization(sender, instance, created, **kwargs):
    """Организация не может существовать без записи своей программы.

    Иначе начисление отвечает 422 PROGRAM_INACTIVE, а исправить это из
    мобильного приложения нельзя. Значения по умолчанию берутся из модели
    и дальше правятся директором или владельцем заведения.
    """
    if not created:
        return
    from apps.organizations.services import ensure_program
    ensure_program(instance)

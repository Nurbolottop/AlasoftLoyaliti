"""Rate limiting для OTP / PIN / admin login (ТЗ backend §24).

Номер из тела запроса нормализуется до E.164 до построения ключа: иначе
«+996555…», «0555…» и «996 555 …» — разные ключи для одного номера, и лимит
обходится сменой формата.
"""

import re

from rest_framework.throttling import SimpleRateThrottle

from apps.common.phone import normalize_phone


def _phone_ident(raw):
    try:
        return normalize_phone(raw)
    except Exception:
        # Невалидный номер дальше отклонит сериализатор; ключ всё равно
        # стабильный, чтобы мусорные запросы тоже упирались в лимит.
        return re.sub(r'\D', '', str(raw)) or str(raw)


class PhoneScopedThrottle(SimpleRateThrottle):
    """Лимит по номеру телефона из тела запроса."""

    scope = 'otp_request_phone'

    def get_cache_key(self, request, view):
        phone = (request.data or {}).get('phone')
        if not phone:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': _phone_ident(phone)}


class IpScopedThrottle(SimpleRateThrottle):
    """Лимит по IP клиента. Реальный IP за nginx — см. NUM_PROXIES в prod.py."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class UserScopedThrottle(SimpleRateThrottle):
    """Лимит на авторизованного пользователя (смена номера, удаление)."""

    def get_cache_key(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}


class OtpIpThrottle(IpScopedThrottle):
    scope = 'otp_request_ip'


class OtpVerifyThrottle(PhoneScopedThrottle):
    scope = 'otp_verify'


class OtpVerifyDailyThrottle(PhoneScopedThrottle):
    """Суточный потолок неверных попыток на номер: новый челлендж раз в
    минуту не даёт перебрать 4-значный код по 5 попыток за раз."""

    scope = 'otp_verify_day'


class AuthIpThrottle(IpScopedThrottle):
    """Общий лимит с одного IP на проверку кода, PIN и пароля директора."""

    scope = 'auth_ip'


class PinLoginThrottle(PhoneScopedThrottle):
    scope = 'pin_login'


class DirectorLoginThrottle(PhoneScopedThrottle):
    scope = 'director_login'


class UserOtpRequestThrottle(UserScopedThrottle):
    scope = 'otp_request_user'


class UserOtpVerifyThrottle(UserScopedThrottle):
    scope = 'otp_verify_user'

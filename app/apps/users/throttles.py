"""Rate limiting для OTP / PIN / admin login (ТЗ backend §24)."""

from rest_framework.throttling import SimpleRateThrottle


class PhoneScopedThrottle(SimpleRateThrottle):
    """Лимит по номеру телефона из тела запроса."""

    scope = 'otp_request_phone'

    def get_cache_key(self, request, view):
        phone = (request.data or {}).get('phone')
        if not phone:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': str(phone)}


class OtpIpThrottle(SimpleRateThrottle):
    scope = 'otp_request_ip'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class OtpVerifyThrottle(PhoneScopedThrottle):
    scope = 'otp_verify'


class PinLoginThrottle(PhoneScopedThrottle):
    scope = 'pin_login'


class DirectorLoginThrottle(PhoneScopedThrottle):
    scope = 'director_login'

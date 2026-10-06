"""IP клиента за обратным прокси."""

from django.conf import settings


def client_ip(request):
    """Тот же IP, что использует rate limiting (DRF get_ident).

    Без доверенных прокси X-Forwarded-For не читается: заголовок ставит
    кто угодно. С NUM_PROXIES=N берётся N-й адрес справа — его дописал наш
    nginx, левее лежит то, что прислал клиент.
    """
    if request is None:
        return None
    remote_addr = request.META.get('REMOTE_ADDR')
    num_proxies = settings.REST_FRAMEWORK.get('NUM_PROXIES')
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if num_proxies and forwarded:
        hops = [h.strip() for h in forwarded.split(',') if h.strip()]
        if hops:
            return hops[-min(num_proxies, len(hops))]
    return remote_addr

"""Денежная арифметика.

Все суммы — целые числа в тыйынах (1 сом = 100 тыйын), проценты — в basis
points (500 bps = 5%). float не используется нигде (ТЗ backend §2, §24).
"""

BPS_DENOMINATOR = 10_000
TIYIN_IN_SOM = 100


def apply_bps(amount_tiyin: int, rate_bps: int) -> int:
    """Процент от суммы с округлением вниз — в пользу системы, без float."""
    if amount_tiyin <= 0 or rate_bps <= 0:
        return 0
    return (int(amount_tiyin) * int(rate_bps)) // BPS_DENOMINATOR


def som_to_tiyin(som) -> int:
    return int(round(float(som) * TIYIN_IN_SOM))


def tiyin_to_som_str(amount_tiyin: int) -> str:
    amount_tiyin = int(amount_tiyin or 0)
    sign = '-' if amount_tiyin < 0 else ''
    amount_tiyin = abs(amount_tiyin)
    return f'{sign}{amount_tiyin // TIYIN_IN_SOM}.{amount_tiyin % TIYIN_IN_SOM:02d}'


def bps_to_percent_str(rate_bps: int) -> str:
    rate_bps = int(rate_bps or 0)
    whole, frac = divmod(rate_bps, 100)
    return f'{whole}' if frac == 0 else f'{whole}.{frac:02d}'.rstrip('0')

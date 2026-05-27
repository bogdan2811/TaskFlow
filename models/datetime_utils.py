from datetime import timezone


def utc_isoformat(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + 'Z'
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

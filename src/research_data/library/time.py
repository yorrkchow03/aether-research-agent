"""Library dates are UTC regardless of the machine's local timezone."""
from datetime import datetime,timezone
from decimal import Decimal

def timestamp(value):
 if isinstance(value,(int,float,Decimal)) or str(value).isdigit():return datetime.fromtimestamp(float(value),timezone.utc)
 at=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
 if at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
 return at.astimezone(timezone.utc)

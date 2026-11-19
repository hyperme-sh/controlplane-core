"""Read-only owner-local archive queue marks; never access a receiver's database."""
from uuid import UUID

from sqlalchemy import text


def queue_status(engine, tenant):
    if type(tenant) is not UUID or not tenant.int:
        raise ValueError('Declared tenant required')
    with engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant': str(tenant)})
        row = db.execute(text('''SELECT count(*) AS pending,
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - min(created_at))) AS oldest_age,
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - max(created_at))) AS newest_age
            FROM service_outbox WHERE tenant_id=:tenant AND delivered_at IS NULL'''),
            {'tenant': tenant}).mappings().one()
        pending, oldest, newest = row['pending'], row['oldest_age'], row['newest_age']
        if type(pending) is not int or pending < 0:
            raise ValueError('Invalid archive queue count')
        if pending == 0:
            if oldest is not None or newest is not None:
                raise ValueError('Invalid empty archive queue')
            return {'pending': 0, 'oldest_age_seconds': None}
        import math
        if oldest is None or newest is None or not math.isfinite(float(oldest)) or not math.isfinite(float(newest)) or not 0 <= newest <= oldest:
            raise ValueError('Invalid archive queue time')
        return {'pending': pending, 'oldest_age_seconds': float(oldest)}

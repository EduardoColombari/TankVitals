"""Initial relational registry; upgrades the former compose SQL bootstrap."""
from alembic import op
from sqlalchemy import inspect, Column, String, JSON, Boolean, Integer, DateTime
from app.core.catalog import metadata

revision = '0001'
down_revision = None

def upgrade():
    bind = op.get_bind()
    # Existing installations may already have the original IoT tables.
    existing = inspect(bind).get_table_names()
    if 'devices' in existing:
        cols = {c['name'] for c in inspect(bind).get_columns('devices')}
        if 'name' not in cols:
            op.add_column('devices', Column('name', String(120), nullable=False, server_default='Dispositivo'))
        if 'commands' not in cols:
            op.add_column('devices', Column('commands', JSON, nullable=False, server_default='[]'))
            op.execute("UPDATE devices SET commands = '[\"on\"]' WHERE kind = 'actuator'")
            op.execute("UPDATE devices SET commands = '[\"on\",\"inject_fault\",\"repair\"]' WHERE kind = 'equipment'")
    if 'device_fields' in existing:
        if 'active' not in {c['name'] for c in inspect(bind).get_columns('device_fields')}:
            op.add_column('device_fields', Column('active', Boolean, nullable=False, server_default='true'))
        cols = {c['name'] for c in inspect(bind).get_columns('device_fields')}
        for name, kind, default in [('short', String(80), ''), ('unit', String(32), ''), ('decimals', Integer(), '2')]:
            if name not in cols:
                op.add_column('device_fields', Column(name, kind, nullable=False, server_default=default))
    if 'rules' in existing:
        cols = {c['name'] for c in inspect(bind).get_columns('rules')}
        for name, kind in [('updated_at', DateTime(timezone=True)), ('updated_by', String(120))]:
            if name not in cols:
                op.add_column('rules', Column(name, kind))
    metadata.create_all(bind)

def downgrade():
    metadata.drop_all(op.get_bind())

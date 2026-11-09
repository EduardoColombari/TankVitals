"""Relational schema shared by migrations and the API."""
from datetime import datetime, timezone
from sqlalchemy import (MetaData, Table, Column, String, Integer, Float, Boolean,
                        DateTime, JSON, ForeignKey, Text, CheckConstraint)

metadata = MetaData()
def now():
    return datetime.now(timezone.utc)

users = Table('users', metadata,
    Column('username', String(120), primary_key=True),
    Column('password_hash', String(100), nullable=False),
    Column('role', String(20), nullable=False),
    Column('active', Boolean, nullable=False, default=True),
    Column('failures', Integer, nullable=False, default=0),
    Column('locked_until', DateTime(timezone=True)),
    CheckConstraint("role IN ('viewer','operator','admin')"))
devices = Table('devices', metadata,
    Column('id', String(80), primary_key=True), Column('name', String(120), nullable=False),
    Column('zone', String(80), nullable=False), Column('kind', String(24), nullable=False),
    Column('commands', JSON, nullable=False, default=list),
    Column('active', Boolean, nullable=False, default=True))
fields = Table('device_fields', metadata,
    Column('device_id', String(80), ForeignKey('devices.id'), primary_key=True),
    Column('key', String(80), primary_key=True), Column('label', String(120), nullable=False),
    Column('short', String(80), nullable=False, default=''),
    Column('unit', String(32), nullable=False, default=''),
    Column('decimals', Integer, nullable=False, default=2),
    *(Column(n, Float) for n in ('ok_min','ok_max','crit_min','crit_max')),
    Column('active', Boolean, nullable=False, default=True))
rules = Table('rules', metadata,
    Column('id', String(80), primary_key=True), Column('name', String(120), nullable=False),
    Column('device_id', String(80), ForeignKey('devices.id'), nullable=False),
    Column('field_key', String(80), nullable=False),
    Column('actuator_id', String(80), ForeignKey('devices.id'), nullable=False),
    Column('on_below', Float, nullable=False), Column('off_at', Float, nullable=False),
    Column('enabled', Boolean, nullable=False, default=True))
rules.append_column(Column('updated_at', DateTime(timezone=True)))
rules.append_column(Column('updated_by', String(120)))
commands = Table('commands', metadata,
    Column('id', Integer, primary_key=True), Column('device_id', String(80), ForeignKey('devices.id'), nullable=False),
    Column('command', String(80), nullable=False), Column('value', JSON),
    Column('origin', String(32), nullable=False), Column('username', String(120)),
    Column('created_at', DateTime(timezone=True), nullable=False))
alerts = Table('alerts', metadata,
    Column('id', Integer, primary_key=True), Column('device_id', String(80), ForeignKey('devices.id'), nullable=False),
    Column('field_key', String(80)), Column('level', String(32), nullable=False),
    Column('message', Text, nullable=False), Column('value', Float),
    Column('opened_at', DateTime(timezone=True), nullable=False, index=True),
    Column('closed_at', DateTime(timezone=True)))
audit = Table('audit', metadata,
    Column('id', Integer, primary_key=True), Column('ts', DateTime(timezone=True), nullable=False, index=True),
    Column('username', String(120), index=True), Column('type', String(40), nullable=False, index=True),
    Column('action', String(80), nullable=False), Column('target', String(120)),
    Column('detail', JSON), Column('ok', Boolean, nullable=False))
# The existing IA service stores job/model documents in this table.
records = Table('ai_records', metadata, Column('id', String(80), primary_key=True),
    Column('kind', String(30), nullable=False, index=True), Column('data', JSON, nullable=False))
jobs = Table('jobs', metadata, Column('id', String(80), primary_key=True),
    Column('kind', String(30), nullable=False), Column('params', JSON, nullable=False),
    Column('status', String(30), nullable=False), Column('progress', Float, nullable=False),
    Column('message', Text), Column('created_by', String(120), ForeignKey('users.username')),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('finished_at', DateTime(timezone=True)), Column('result', JSON))
model_versions = Table('model_versions', metadata, Column('id', String(80), primary_key=True),
    Column('model', String(30), nullable=False), Column('version', String(80), nullable=False),
    Column('metrics', JSON), Column('baseline', JSON), Column('active', Boolean, default=False, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('job_id', String(80), ForeignKey('jobs.id')))

def log(conn, username, kind, action, target=None, detail=None, ok=True):
    conn.execute(audit.insert().values(ts=now(), username=username, type=kind,
                 action=action, target=target, detail=detail, ok=ok))

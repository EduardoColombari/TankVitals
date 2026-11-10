"""BACK acceptance: persisted auth, roles, registry, migrations and WS tickets."""
from contextlib import asynccontextmanager
import time
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine, select, func
from sqlalchemy.pool import StaticPool

from app.core.catalog import metadata, users, devices, audit
from app.core.seed import seed
from app.core.pg import Database
from app.main import create_app

@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'acceptance-secret-at-least-32-characters')
    for role in ('viewer','operator','admin'):
        monkeypatch.setenv(f'DEMO_{role.upper()}_PASSWORD', 'demo-' + role)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
    metadata.create_all(engine)
    seed(engine)
    app = create_app()
    app.state.database = Database(engine=engine)
    from app.api import ConnectionManager
    app.state.manager = ConnectionManager()
    # Avoid starting external services; each route uses the real relational store.
    @asynccontextmanager
    async def local_lifespan(app):
        yield
    app.router.lifespan_context = local_lifespan
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, app=app)
    engine.dispose()

def login(registry, role='admin'):
    response = registry.client.post('/api/auth/login', data={'username':role, 'password':'demo-' + role})
    assert response.status_code == 200
    registry.client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
    return response.json()['access_token']


def test_assistant_uses_shared_login_database_and_audit(registry, monkeypatch):
    import httpx
    from unittest.mock import Mock
    from app.assistant import router as assistant

    registry.app.state.repo = Mock()
    registry.app.state.repo._query.return_value = []
    monkeypatch.setattr(assistant, 'client', lambda: httpx.AsyncClient(
        base_url='http://ollama', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='{"message":{"content":"Sem leituras."}}\n{"done":true}\n'))))
    login(registry, 'viewer')
    response = registry.client.post('/api/assistant', json={'question': 'Como estão os tanques?'})
    assert response.status_code == 200
    assert 'Sem leituras.' in response.text
    login(registry, 'admin')
    response = registry.client.get('/api/audit', params={'type': 'assistant'})
    assert response.status_code == 200
    with registry.engine.connect() as conn:
        row = conn.execute(select(audit).where(audit.c.type == 'assistant')).mappings().one()
        assert row['username'] == 'viewer'
        assert row['detail']['question'] == 'Como estão os tanques?'

def test_seed_does_not_duplicate_or_overwrite(registry):
    with registry.engine.begin() as conn:
        conn.execute(devices.update().where(devices.c.id == 'od-01').values(name='Editado'))
        original = conn.execute(select(users.c.password_hash).where(users.c.username == 'admin')).scalar_one()
    seed(registry.engine)
    with registry.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(devices)).scalar_one() == 9
        assert conn.execute(select(devices.c.name).where(devices.c.id == 'od-01')).scalar_one() == 'Editado'
        assert conn.execute(select(users.c.password_hash).where(users.c.username == 'admin')).scalar_one() == original

def test_sixth_failure_is_locked_and_all_attempts_audited(registry):
    for _ in range(5):
        assert registry.client.post('/api/auth/login', json={'username':'viewer','password':'wrong'}).status_code == 401
    assert registry.client.post('/api/auth/login', json={'username':'viewer','password':'demo-viewer'}).status_code == 429
    with registry.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(audit)).scalar_one() == 6
        assert conn.execute(select(users.c.password_hash).where(users.c.username == 'viewer')).scalar_one().startswith('$2')

def test_viewer_cannot_command_or_edit(registry):
    login(registry, 'viewer')
    assert registry.client.post('/api/devices/aerador-01/command', json={'command':'on','value':True}).status_code == 403
    assert registry.client.delete('/api/devices/od-01').status_code == 403
    assert registry.client.get('/api/audit').status_code == 403
    assert registry.client.get('/api/devices').status_code == 200

def test_registry_range_rule_and_audit_contract(registry):
    login(registry)
    client = registry.client
    device = next(d for d in client.get('/api/devices').json() if d['id'] == 'od-01')
    assert device['fields'][0]['range']['ok_min'] == 5
    body = {k:device[k] for k in ('name','zone','active','fields')}
    body['fields'][0]['range']['ok_min'] = 5.5
    assert client.put('/api/devices/od-01', json=body).status_code == 200
    assert next(d for d in client.get('/api/devices').json() if d['id'] == 'od-01')['fields'][0]['range']['ok_min'] == 5.5
    assert client.put('/api/rules/aerator-low-oxygen', json={'on_below':4.5,'off_at':6}).status_code == 200
    assert client.put('/api/rules/aerator-low-oxygen', json={'on_below':7,'off_at':6}).status_code == 400
    result = client.get('/api/audit?type=registry&user=admin&page_size=1').json()
    assert result['total'] == 2 and len(result['items']) == 1
    assert result['items'][0]['user'] == 'admin'

def test_ticket_is_required_one_time_and_fans_out(registry):
    login(registry, 'viewer')
    with pytest.raises(WebSocketDisconnect):
        with registry.client.websocket_connect('/ws/live'):
            pass
    first = registry.client.post('/api/ws/ticket').json()['ticket']
    second = registry.client.post('/api/ws/ticket').json()['ticket']
    with registry.client.websocket_connect('/ws/live?ticket=' + first) as a:
        with registry.client.websocket_connect('/ws/live?ticket=' + second) as b:
            event = {'type':'telemetry','device':'od-01','payload':{'oxygen_mg_l':6}}
            started = time.monotonic()
            a.portal.call(registry.app.state.manager.broadcast, event)
            assert a.receive_json() == b.receive_json() == event
            assert time.monotonic() - started < 1
    with pytest.raises(WebSocketDisconnect):
        with registry.client.websocket_connect('/ws/live?ticket=' + first):
            pass

def test_alembic_upgrade_repeatable(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + (tmp_path / 'migration.db').as_posix())
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    from sqlalchemy import inspect
    engine = create_engine('sqlite:///' + (tmp_path / 'migration.db').as_posix())
    assert {'users','devices','device_fields','rules','commands','alerts','audit','jobs','model_versions'} <= set(inspect(engine).get_table_names())
    engine.dispose()


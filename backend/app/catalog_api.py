"""Registry, ranges, rules, alerts and audit endpoints."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.core import catalog as db
from app.core.auth import require_role

router = APIRouter(prefix='/api')
viewer, admin = require_role('viewer'), require_role('admin')

def engine(request: Request):
    value = getattr(request.app.state, 'database', None)
    if value is None:
        raise HTTPException(503, 'PostgreSQL não configurado')
    return value.engine

class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Bounds(Body):
    ok_min: float | None = None
    ok_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None

    @model_validator(mode='after')
    def ordered(self):
        values = [v for v in (self.crit_min, self.ok_min, self.ok_max, self.crit_max) if v is not None]
        if values != sorted(values):
            raise ValueError('Limites devem estar em ordem crescente')
        return self

class DeviceField(Body):
    key: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]{0,79}$')
    label: str = Field(min_length=1, max_length=120)
    short: str = Field('', max_length=80)
    unit: str = Field('', max_length=32)
    decimals: int = Field(2, ge=0, le=8)
    range: Bounds | None = None

class Device(Body):
    id: str = Field(pattern=r'^[a-z0-9-]{1,80}$')
    name: str = Field(min_length=1, max_length=120)
    zone: str = Field(pattern=r'^[a-z0-9-]{1,80}$')
    kind: Literal['sensor','equipment','actuator']
    commands: list[Literal['on','repair','inject_fault']] = Field(default_factory=list)
    active: bool = True
    fields: list[DeviceField] = Field(default_factory=list)

class DeviceUpdate(Body):
    name: str = Field(min_length=1, max_length=120)
    zone: str = Field(pattern=r'^[a-z0-9-]{1,80}$')
    active: bool
    fields: list[DeviceField]

class DeviceEdit(Body):
    name: str | None = Field(None, min_length=1, max_length=120)
    zone: str | None = Field(None, pattern=r'^[a-z0-9-]{1,80}$')
    active: bool | None = None

class Range(Body):
    label: str = Field(min_length=1, max_length=120)
    ok_min: float | None = None
    ok_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None
    active: bool = True

    @model_validator(mode='after')
    def ordered(self):
        values = [v for v in (self.crit_min, self.ok_min, self.ok_max, self.crit_max) if v is not None]
        if values != sorted(values):
            raise ValueError('Limites devem estar em ordem crescente')
        return self

class Rule(Body):
    id: str = Field(pattern=r'^[a-z0-9-]{1,80}$')
    name: str = Field(min_length=1, max_length=120)
    device_id: str
    field_key: str
    actuator_id: str
    on_below: float
    off_at: float
    enabled: bool = True

    @model_validator(mode='after')
    def hysteresis(self):
        if self.on_below >= self.off_at:
            raise ValueError('off_at deve ser maior que on_below')
        return self

@router.get('/devices')
def devices(e=Depends(engine), user=Depends(viewer)):
    with e.connect() as conn:
        result = [dict(r) for r in conn.execute(select(db.devices).order_by(db.devices.c.id)).mappings()]
        ranges = [dict(r) for r in conn.execute(select(db.fields).where(db.fields.c.active.is_(True))).mappings()]
    for d in result:
        d['fields'] = [present_field(r) for r in ranges if r['device_id'] == d['id']]
        d['state_field'] = {'actuator':'on', 'equipment':'running'}.get(d['kind'])
        d['control'] = ('rule' if d['id'] == 'aerador-01' else 'manual') if d['kind'] == 'actuator' else None
    return result

def present_field(r):
    bounds = {k:r[k] for k in ('ok_min','ok_max','crit_min','crit_max')}
    return {k:r[k] for k in ('key','label','short','unit','decimals')} | {
        'range':bounds if any(v is not None for v in bounds.values()) else None}

def write_fields(conn, device_id, values):
    keys = [f.key for f in values]
    if len(keys) != len(set(keys)):
        raise HTTPException(400, 'Variável repetida')
    conn.execute(db.fields.update().where(db.fields.c.device_id == device_id).values(active=False))
    for field in values:
        data = field.model_dump(exclude={'range'})
        data.update(field.range.model_dump() if field.range else Bounds().model_dump())
        condition = (db.fields.c.device_id == device_id) & (db.fields.c.key == field.key)
        if not conn.execute(db.fields.update().where(condition).values(**data, active=True)).rowcount:
            conn.execute(db.fields.insert().values(device_id=device_id, **data, active=True))

def device_result(e, user, device_id):
    return next(d for d in devices(e, user) if d['id'] == device_id)

def save(e, table, condition, data, actor, target, create=False):
    try:
        with e.begin() as conn:
            if create:
                conn.execute(table.insert().values(**data))
            elif not conn.execute(table.update().where(condition).values(**data)).rowcount:
                raise HTTPException(404, 'Registro não encontrado')
            db.log(conn, actor['sub'], 'registry', 'create' if create else 'update', target, data)
    except IntegrityError:
        raise HTTPException(409, 'Registro duplicado ou referência inválida') from None
    return data

@router.post('/devices', status_code=201)
def create_device(body: Device, e=Depends(engine), user=Depends(admin)):
    data = body.model_dump(exclude={'fields','commands'})
    data['commands'] = {'sensor':[], 'actuator':['on'], 'equipment':['on','repair','inject_fault']}[body.kind]
    try:
        with e.begin() as conn:
            conn.execute(db.devices.insert().values(**data))
            write_fields(conn, body.id, body.fields)
            db.log(conn, user['sub'], 'registry', 'device_created', body.id, body.model_dump())
    except IntegrityError:
        raise HTTPException(409, 'Dispositivo já cadastrado') from None
    return device_result(e, user, body.id)

@router.put('/devices/{device_id}')
def update_device(device_id: str, body: DeviceUpdate, e=Depends(engine), user=Depends(admin)):
    with e.begin() as conn:
        if not conn.execute(db.devices.update().where(db.devices.c.id == device_id).values(
                **body.model_dump(exclude={'fields'}))).rowcount:
            raise HTTPException(404, 'Dispositivo não encontrado')
        write_fields(conn, device_id, body.fields)
        db.log(conn, user['sub'], 'registry', 'device_updated' if body.active else 'device_deactivated',
               device_id, body.model_dump())
    return device_result(e, user, device_id)

@router.patch('/devices/{device_id}')
def edit_device(device_id: str, body: DeviceEdit, e=Depends(engine), user=Depends(admin)):
    data = body.model_dump(exclude_unset=True)
    if not data or any(v is None for v in data.values()):
        raise HTTPException(400, 'Informe valores válidos')
    return save(e, db.devices, db.devices.c.id == device_id, data, user, device_id)

@router.delete('/devices/{device_id}')
def disable_device(device_id: str, e=Depends(engine), user=Depends(admin)):
    return save(e, db.devices, db.devices.c.id == device_id, {'active':False}, user, device_id)

@router.put('/devices/{device_id}/fields/{key}')
def set_range(device_id: str, key: str, body: Range, e=Depends(engine), user=Depends(admin)):
    if not key.isidentifier() or len(key) > 80:
        raise HTTPException(400, 'Variável inválida')
    condition = (db.fields.c.device_id == device_id) & (db.fields.c.key == key)
    data = {**body.model_dump(), 'device_id':device_id, 'key':key}
    with e.connect() as conn:
        exists = conn.execute(select(db.fields.c.key).where(condition)).first()
    return save(e, db.fields, condition, data, user, f'{device_id}/{key}', not exists)

@router.delete('/devices/{device_id}/fields/{key}')
def disable_range(device_id: str, key: str, e=Depends(engine), user=Depends(admin)):
    return save(e, db.fields, (db.fields.c.device_id == device_id) & (db.fields.c.key == key),
                {'active':False}, user, f'{device_id}/{key}')

@router.get('/rules')
def list_rules(e=Depends(engine), user=Depends(viewer)):
    with e.connect() as conn:
        return [present_rule(dict(r)) for r in conn.execute(select(db.rules)).mappings()]

def present_rule(row):
    for source, target in [('device_id','device'), ('field_key','field'), ('actuator_id','actuator')]:
        row[target] = row.pop(source)
    return row

class RuleUpdate(Body):
    on_below: float
    off_at: float

    @model_validator(mode='after')
    def hysteresis(self):
        if self.on_below >= self.off_at:
            raise ValueError('off_at deve ser maior que on_below')
        return self

def check_rule(e, body):
    with e.connect() as conn:
        field = conn.execute(select(db.fields).where(db.fields.c.device_id == body.device_id,
                             db.fields.c.key == body.field_key)).first()
        actuator = conn.execute(select(db.devices).where(db.devices.c.id == body.actuator_id,
                                db.devices.c.kind == 'actuator')).first()
    if not field or not actuator:
        raise HTTPException(400, 'Sensor, variável ou atuador inválido')

@router.post('/rules', status_code=201)
def create_rule(body: Rule, e=Depends(engine), user=Depends(admin)):
    check_rule(e, body)
    return save(e, db.rules, None, body.model_dump(), user, body.id, True)

@router.put('/rules/{rule_id}')
def edit_rule(rule_id: str, body: RuleUpdate, e=Depends(engine), user=Depends(admin)):
    with e.begin() as conn:
        data = {**body.model_dump(), 'updated_at':db.now(), 'updated_by':user['sub']}
        if not conn.execute(db.rules.update().where(db.rules.c.id == rule_id).values(**data)).rowcount:
            raise HTTPException(404, 'Regra não encontrada')
        db.log(conn, user['sub'], 'registry', 'rule_updated', rule_id, body.model_dump())
        row = dict(conn.execute(select(db.rules).where(db.rules.c.id == rule_id)).mappings().one())
    return present_rule(row)

@router.get('/alerts')
def alerts(status: Literal['open','closed','all']='all', limit: int=Query(50, ge=1, le=200),
           offset: int=Query(0, ge=0), e=Depends(engine), user=Depends(viewer)):
    query = select(db.alerts).order_by(db.alerts.c.opened_at.desc(), db.alerts.c.id.desc())
    if status != 'all':
        query = query.where(db.alerts.c.closed_at.is_(None) if status == 'open' else db.alerts.c.closed_at.is_not(None))
    with e.connect() as conn:
        return [{**dict(r), 'id':str(r['id']), 'device':r['device_id'], 'field':r['field_key']}
                for r in conn.execute(query.limit(limit).offset(offset)).mappings()]

@router.get('/audit')
def audit(type: str | None=None, username: str | None=Query(None, alias='user'),
          page: int=Query(1, ge=1), page_size: int=Query(25, ge=1, le=200),
          e=Depends(engine), user=Depends(admin)):
    query = select(db.audit).order_by(db.audit.c.ts.desc(), db.audit.c.id.desc())
    if type:
        query = query.where(db.audit.c.type == type)
    if username:
        query = query.where(db.audit.c.username == username)
    with e.connect() as conn:
        import json
        total = conn.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        items = [{**dict(r), 'id':str(r['id']), 'user':r['username'],
                  'detail':r['detail'] if isinstance(r['detail'], str) else json.dumps(r['detail'], ensure_ascii=False) if r['detail'] is not None else None}
                 for r in conn.execute(query.limit(page_size).offset((page-1)*page_size)).mappings()]
        return {'items':items, 'total':total, 'page':page, 'page_size':page_size}

@router.get('/history')
def telemetry_history(request: Request, range: Literal['1h','6h','24h','7d']='24h',
                      device: list[str] | None=Query(None), user=Depends(viewer)):
    from app.telemetry_history import query_history
    from app.config import settings
    return query_history(request.app.state.repo.query_api, settings.influx_bucket, range, device)

@router.get('/devices/{device_id}/history')
def history(device_id: str, request: Request, variable: str, period: Literal['1h','24h','7d']='24h',
            e=Depends(engine), user=Depends(viewer)):
    with e.connect() as conn:
        if not conn.execute(select(db.fields.c.key).where(db.fields.c.device_id == device_id,
                            db.fields.c.key == variable)).first():
            raise HTTPException(404, 'Dispositivo ou variável não encontrado')
    return request.app.state.repo.device_history(device_id, variable, period)

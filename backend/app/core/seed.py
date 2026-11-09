"""Idempotent initial registry; never overwrites edited values or passwords."""
import os
import bcrypt
from sqlalchemy import select
from app.core.catalog import devices, fields, rules, users
from app.simulator.devices import DEVICES

RANGES = {
 'temperature_c': (24,28,22,30), 'ph': (6.5,8,6,8.5),
 'level_pct': (30,90,15,98), 'turbidity_ntu': (0,40,0,60),
 'oxygen_mg_l': (5,14,3,16), 'light_lux': (0,40000,0,50000),
 'vibration_mm_s': (0,4.5,0,7), 'current_a': (0,5,0,8),
 'motor_temp_c': (0,70,0,90), 'flow_l_min': (5,30,0,40)}
UNITS = {'temperature_c':'°C', 'ph':'', 'level_pct':'%', 'turbidity_ntu':'NTU',
         'oxygen_mg_l':'mg/L', 'light_lux':'lx', 'vibration_mm_s':'mm/s',
         'current_a':'A', 'motor_temp_c':'°C', 'flow_l_min':'L/min', 'on':''}

def seed(engine):
    with engine.begin() as conn:
        for d in DEVICES:
            if not conn.execute(select(devices.c.id).where(devices.c.id == d.id)).first():
                conn.execute(devices.insert().values(id=d.id, name=d.id, zone=d.zone,
                             kind=d.kind, commands=sorted(d.commands), active=True))
            for key, _ in d.fields:
                if not conn.execute(select(fields.c.key).where(fields.c.device_id == d.id, fields.c.key == key)).first():
                    bounds = dict(zip(('ok_min','ok_max','crit_min','crit_max'), RANGES.get(key, (None,)*4)))
                    conn.execute(fields.insert().values(device_id=d.id, key=key, label=key, short=key,
                        unit=UNITS.get(key,''), decimals=dict(d.fields)[key], active=True, **bounds))
        if not conn.execute(select(rules.c.id).where(rules.c.id == 'aerator-low-oxygen')).first():
            conn.execute(rules.insert().values(id='aerator-low-oxygen', name='Aerador por oxigênio baixo',
                device_id='od-01', field_key='oxygen_mg_l', actuator_id='aerador-01', on_below=5, off_at=6.5, enabled=True))
        for role in ('viewer','operator','admin'):
            if not conn.execute(select(users.c.username).where(users.c.username == role)).first():
                password = os.environ[f'DEMO_{role.upper()}_PASSWORD']
                conn.execute(users.insert().values(username=role, role=role, active=True, failures=0,
                    password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()))

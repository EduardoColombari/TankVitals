CREATE TABLE IF NOT EXISTS devices (
  id varchar(80) PRIMARY KEY, zone varchar(80) NOT NULL, kind varchar(24) NOT NULL, active boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS device_fields (
  device_id varchar(80) NOT NULL REFERENCES devices(id), key varchar(80) NOT NULL, label varchar(120) NOT NULL,
  ok_min double precision, ok_max double precision, crit_min double precision, crit_max double precision,
  PRIMARY KEY (device_id, key)
);
CREATE TABLE IF NOT EXISTS rules (
  id varchar(80) PRIMARY KEY, name varchar(120) NOT NULL, device_id varchar(80) NOT NULL REFERENCES devices(id),
  field_key varchar(80) NOT NULL, actuator_id varchar(80) NOT NULL REFERENCES devices(id),
  on_below double precision NOT NULL, off_at double precision NOT NULL, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS commands (
  id bigserial PRIMARY KEY, device_id varchar(80) NOT NULL, command varchar(80) NOT NULL,
  value jsonb, origin varchar(32) NOT NULL, username varchar(120), created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id bigserial PRIMARY KEY, device_id varchar(80) NOT NULL, field_key varchar(80), level varchar(32) NOT NULL,
  message text NOT NULL, value double precision, opened_at timestamptz NOT NULL, closed_at timestamptz
);

INSERT INTO devices(id, zone, kind) VALUES
 ('temp-agua-01','tanque-01','sensor'),('ph-01','tanque-01','sensor'),('nivel-01','tanque-01','sensor'),
 ('turbidez-01','tanque-01','sensor'),('od-01','tanque-01','sensor'),('luz-01','tanque-01','sensor'),
 ('bomba-rec-01','tanque-01','equipment'),('aerador-01','tanque-01','actuator'),('bomba-repo-01','tanque-01','actuator')
ON CONFLICT (id) DO NOTHING;
INSERT INTO device_fields(device_id,key,label,ok_min,ok_max,crit_min,crit_max) VALUES
 ('temp-agua-01','temperature_c','Temperatura da água',24,28,22,30),('ph-01','ph','pH',6.5,8,6,8.5),
 ('nivel-01','level_pct','Nível',30,90,15,98),('turbidez-01','turbidity_ntu','Turbidez',0,40,0,60),
 ('od-01','oxygen_mg_l','Oxigênio dissolvido',5,14,3,16),('luz-01','light_lux','Luminosidade',0,40000,0,50000),
 ('bomba-rec-01','vibration_mm_s','Vibração',0,4.5,0,7),('bomba-rec-01','current_a','Corrente',0,5,0,8),
 ('bomba-rec-01','motor_temp_c','Temperatura do motor',0,70,0,90),('bomba-rec-01','flow_l_min','Vazão',5,30,0,40)
ON CONFLICT (device_id,key) DO NOTHING;
INSERT INTO rules(id,name,device_id,field_key,actuator_id,on_below,off_at)
VALUES ('aerator-low-oxygen','Aerador por oxigênio baixo','od-01','oxygen_mg_l','aerador-01',5,6.5)
ON CONFLICT (id) DO NOTHING;

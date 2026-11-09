"""Bounded aggregate plots and raw statistics from the telemetry measurement."""
from datetime import datetime, timedelta, timezone
from app.influx_repo import RepositoryUnavailable

PERIODS = {'1h':3600, '6h':21600, '24h':86400, '7d':604800}

def query_history(api, bucket, period, devices=None, variable=None):
    if period not in PERIODS:
        raise ValueError('Período inválido')
    end = datetime.now(timezone.utc)
    step = max(10, PERIODS[period] // 360)
    params = {'bucket':bucket, 'period':'-' + period, 'devices':devices or [],
              'variable':variable or '', 'step':f'{step}s'}
    base = '''from(bucket: params.bucket) |> range(start: duration(v: params.period))
      |> filter(fn: (r) => r._measurement == "telemetry")
      |> filter(fn: (r) => length(arr: params.devices) == 0 or contains(value: r.device, set: params.devices))
      |> filter(fn: (r) => params.variable == "" or r._field == params.variable)
      |> group(columns: ["device", "_field"])
    '''
    try:
        tables = api.query(base + '|> aggregateWindow(every: duration(v: params.step), fn: mean, createEmpty: false)', params=params)
        stats = {}
        # Separate yields preserve the integer type of count in one network request.
        flux = 'base = ' + base + '\n' + '\n'.join(
            f'base |> {op}() |> yield(name: "{op}")' for op in ('count','min','max','mean','last'))
        for table in api.query(flux, params=params):
            for r in table.records:
                key = (r.values['device'], r.get_field())
                op = r.values['result']
                stats.setdefault(key, {})['avg' if op == 'mean' else op] = r.get_value()
    except Exception as exc:
        raise RepositoryUnavailable('Histórico indisponível') from exc
    series = []
    for table in tables:
        if table.records:
            r = table.records[0]
            key = (r.values['device'], r.get_field())
            series.append({'device':key[0], 'field':key[1], 'stats':stats.get(key, {}),
                           'points':[{'t':r.get_time().isoformat(), 'v':r.get_value()} for r in table.records]})
    return {'range':period, 'from':(end-timedelta(seconds=PERIODS[period])).isoformat(),
            'to':end.isoformat(), 'step_s':step, 'series':series}

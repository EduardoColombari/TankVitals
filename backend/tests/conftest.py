"""Testes isolados de credenciais e serviços reais."""
import os
os.environ["INFLUX_TOKEN"] = "test-token"
from app import config

config.settings = config.Settings(_env_file=None, **{
    name: ("test-token" if name == "influx_token" else field.default)
    for name, field in config.Settings.model_fields.items()
})

"""Shared deployment configuration for relational services."""
import os
from app.core.pg import build_url

def database_url():
    explicit = os.getenv('DATABASE_URL') or os.getenv('AI_DATABASE_URL')
    if explicit:
        return explicit
    return build_url(host=os.getenv('POSTGRES_HOST','postgres'), port=int(os.getenv('POSTGRES_PORT','5432')),
                     user=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD'],
                     database=os.environ['POSTGRES_DB'])

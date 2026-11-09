"""JWT roles and persistent per-account login throttling."""
import os
from datetime import timedelta
import bcrypt
import jwt
from fastapi import Depends, HTTPException
from sqlalchemy import select, update
from app.ai_api import current_user
from app.core.catalog import users, log, now

def require_role(role):
    ranks = {'viewer': 0, 'operator': 1, 'admin': 2}
    def dependency(user=Depends(current_user)):
        if ranks[user['role']] < ranks[role]:
            raise HTTPException(403, 'Papel insuficiente')
        return user
    return dependency

def authenticate(engine, username, password):
    secret = os.environ.get('JWT_SECRET')
    if not secret:
        raise HTTPException(503, 'Autenticação não configurada')
    username = {'visualizador':'viewer','operador':'operator'}.get(username, username)
    status = 401
    with engine.begin() as conn:
        row = conn.execute(select(users).where(users.c.username == username).with_for_update()).mappings().first()
        stamp = now()
        locked = row and row['locked_until']
        if locked and locked.tzinfo is None:
            locked = locked.replace(tzinfo=stamp.tzinfo)
        if locked and locked > stamp:
            status = 429
            log(conn, username, 'login', 'login_blocked', ok=False)
        else:
            try:
                valid = bool(row and row['active'] and bcrypt.checkpw(password.encode(), row['password_hash'].encode()))
            except ValueError:
                valid = False
            if row:
                failures = 0 if valid else (0 if locked else row['failures']) + 1
                conn.execute(update(users).where(users.c.username == username).values(
                    failures=failures, locked_until=stamp + timedelta(minutes=5) if failures >= 5 else None))
            log(conn, username, 'login', 'login' if valid else 'login_failed', ok=valid)
            if valid:
                return {'access_token': jwt.encode({'sub': username, 'role': row['role'], 'iat': stamp,
                        'exp': stamp + timedelta(minutes=30)}, secret, algorithm='HS256'), 'token_type':'bearer'}
    raise HTTPException(status, 'Muitas tentativas' if status == 429 else 'Usuário ou senha inválidos')

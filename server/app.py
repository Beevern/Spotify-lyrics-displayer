#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify Lyrics Proxy Server
============================
Holds YOUR Spotify app credentials. Clients authenticate their own Spotify
accounts through this server — they never need their own Spotify API keys.

Required environment variables (set in .env or hosting dashboard):
  SPOTIFY_CLIENT_ID
  SPOTIFY_CLIENT_SECRET
  SERVER_BASE_URL      e.g. https://yourapp.railway.app

Run locally:
  pip install -r requirements.txt
  python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload

Deploy (Railway / Render / Fly.io):
  - Set the three env vars above in the hosting dashboard
  - The Procfile handles startup automatically
"""

import os
import time
import sqlite3
from pathlib import Path
from urllib.parse import urlencode
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse

# ── Config ────────────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.environ['SPOTIFY_CLIENT_ID']
SPOTIFY_CLIENT_SECRET = os.environ['SPOTIFY_CLIENT_SECRET']
BASE_URL              = os.environ.get('SERVER_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
REDIRECT_URI          = f'{BASE_URL}/callback'

SCOPE = (
    'user-read-currently-playing '
    'user-read-playback-state '
    'user-modify-playback-state'
)

# ── Token storage (SQLite) ────────────────────────────────────────────────────
DB = Path('tokens.db')

def _db():
    return sqlite3.connect(DB)

def _init_db():
    with _db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS tokens (
            client_id     TEXT PRIMARY KEY,
            access_token  TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at    REAL NOT NULL
        )''')

def _get(client_id: str) -> dict | None:
    with _db() as c:
        row = c.execute(
            'SELECT access_token, refresh_token, expires_at FROM tokens WHERE client_id=?',
            (client_id,)).fetchone()
    if row:
        return {'access_token': row[0], 'refresh_token': row[1], 'expires_at': row[2]}
    return None

def _save(client_id: str, entry: dict):
    with _db() as c:
        c.execute(
            'INSERT OR REPLACE INTO tokens VALUES (?,?,?,?)',
            (client_id, entry['access_token'], entry['refresh_token'], entry['expires_at']))


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app):
    _init_db()
    yield

app = FastAPI(lifespan=lifespan)


async def _valid_token(client_id: str) -> str | None:
    """Return a valid access token, auto-refreshing if expired."""
    entry = _get(client_id)
    if not entry:
        return None

    if time.time() > entry['expires_at'] - 60:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                'https://accounts.spotify.com/api/token',
                data={
                    'grant_type':    'refresh_token',
                    'refresh_token': entry['refresh_token'],
                },
                auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET))
        d = r.json()
        if 'access_token' not in d:
            return None
        entry['access_token'] = d['access_token']
        entry['expires_at']   = time.time() + d['expires_in']
        if 'refresh_token' in d:
            entry['refresh_token'] = d['refresh_token']
        _save(client_id, entry)

    return entry['access_token']


# ── HTML helpers ──────────────────────────────────────────────────────────────
def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>body{{background:#0d0d0d;color:#fff;font-family:"Segoe UI",sans-serif;
text-align:center;padding-top:100px;margin:0}}
h2{{color:#1DB954}} p{{color:#888}}</style></head>
<body>{body}</body></html>''')


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get('/')
def root():
    return _page('<h2>Spotify Lyrics Server</h2><p>Running.</p>')


@app.get('/login')
def login(client_id: str):
    """Redirect client browser to Spotify OAuth consent screen."""
    params = {
        'client_id':     SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri':  REDIRECT_URI,
        'scope':         SCOPE,
        'state':         client_id,
        'show_dialog':   'false',
    }
    return RedirectResponse(
        'https://accounts.spotify.com/authorize?' + urlencode(params))


@app.get('/callback')
async def callback(code: str = None, state: str = '', error: str = None):
    """Spotify redirects here after the user grants permission."""
    if error or not code:
        return _page(f'<h2 style="color:#ff6b6b">Login failed</h2><p>{error}</p>')

    async with httpx.AsyncClient() as c:
        r = await c.post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type':   'authorization_code',
                'code':         code,
                'redirect_uri': REDIRECT_URI,
            },
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET))
    d = r.json()

    if 'access_token' not in d:
        return _page(f'<h2 style="color:#ff6b6b">Token error</h2><p>{d}</p>')

    _save(state, {
        'access_token':  d['access_token'],
        'refresh_token': d['refresh_token'],
        'expires_at':    time.time() + d['expires_in'],
    })

    return _page('<h2>&#10003; Authenticated!</h2>'
                 '<p>You can close this window and return to the app.</p>')


@app.get('/api/status')
async def status(client_id: str):
    """Client polls this to check if login is complete."""
    return {'authenticated': _get(client_id) is not None}


@app.get('/api/playback')
async def playback(client_id: str):
    """Proxy the Spotify current playback state."""
    token = await _valid_token(client_id)
    if not token:
        return {'error': 'not_authenticated'}

    async with httpx.AsyncClient() as c:
        r = await c.get(
            'https://api.spotify.com/v1/me/player',
            headers={'Authorization': f'Bearer {token}'})

    if r.status_code == 204:
        return {'is_playing': False, 'item': None}
    if r.status_code == 200:
        return r.json()
    return {'error': r.text, 'status': r.status_code}


@app.post('/api/play')
async def play(client_id: str):
    """Resume playback."""
    token = await _valid_token(client_id)
    if not token:
        return {'error': 'not_authenticated'}
    async with httpx.AsyncClient() as c:
        await c.put(
            'https://api.spotify.com/v1/me/player/play',
            headers={'Authorization': f'Bearer {token}'})
    return {'ok': True}


@app.post('/api/pause')
async def pause(client_id: str):
    """Pause playback."""
    token = await _valid_token(client_id)
    if not token:
        return {'error': 'not_authenticated'}
    async with httpx.AsyncClient() as c:
        await c.put(
            'https://api.spotify.com/v1/me/player/pause',
            headers={'Authorization': f'Bearer {token}'})
    return {'ok': True}

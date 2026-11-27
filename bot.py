"""Single-process Discord bot and authenticated Roblox stats API."""
import asyncio
import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from dotenv import load_dotenv


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS links (
                roblox TEXT PRIMARY KEY, discord TEXT UNIQUE NOT NULL,
                osu TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (
                roblox TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL,
                expires REAL NOT NULL, discord TEXT UNIQUE, ticket TEXT UNIQUE,
                state TEXT UNIQUE, cookie TEXT, osu TEXT, username TEXT);
        ''')

    def run(self, sql, args=()):
        with self.db:
            return self.db.execute(sql, args)

    def cleanup(self):
        self.run('DELETE FROM pending WHERE expires <= ?', (time.time(),))

    def approve(self, roblox, osu):
        self.cleanup()
        with self.db:
            row = self.db.execute('SELECT * FROM pending WHERE roblox=? AND osu=?',
                                  (roblox, osu)).fetchone()
            if not row:
                raise ValueError('No matching verified request. Run /osulink again if it expired.')
            self.db.execute('INSERT INTO links VALUES (?,?,?)', (roblox, row['discord'], osu))
            self.db.execute('DELETE FROM pending WHERE roblox=?', (roblox,))


class Service:
    def __init__(self):
        load_dotenv()
        for key in ('DISCORD_TOKEN', 'OSU_CLIENT_ID', 'OSU_CLIENT_SECRET', 'PUBLIC_URL', 'GAME_API_KEY'):
            if not os.getenv(key):
                raise RuntimeError(f'Set {key} in .env')
        self.base = os.environ['PUBLIC_URL'].rstrip('/')
        if not self.base.startswith('https://'):
            raise RuntimeError('PUBLIC_URL must use HTTPS')
        if len(os.environ['GAME_API_KEY']) < 32:
            raise RuntimeError('GAME_API_KEY must be a random secret of at least 32 characters')
        self.store = Store(os.getenv('DATABASE_PATH', 'links.sqlite3'))
        self.cache = {}
        self.limits = {}
        self.access_token = None
        self.access_until = 0
        self.stats_lock = asyncio.Lock()

    def throttle(self, key, seconds):
        now = time.monotonic()
        self.limits = {k: v for k, v in self.limits.items() if v > now}
        if self.limits.get(key, 0) > now:
            raise web.HTTPTooManyRequests(text='Please wait before trying again.')
        self.limits[key] = now + seconds

    async def osu_request(self, method, path, **kwargs):
        async with self.http.request(method, 'https://osu.ppy.sh' + path, **kwargs) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text='osu! is unavailable or authorization failed. Try again.')
            return await response.json()

    async def token(self, **extra):
        return await self.osu_request('POST', '/oauth/token', data={
            'client_id': os.environ['OSU_CLIENT_ID'],
            'client_secret': os.environ['OSU_CLIENT_SECRET'], **extra})

    async def start(self, request):
        self.store.cleanup()
        form = await request.post() if request.method == 'POST' else None
        ticket = form.get('ticket', '') if form is not None else request.query.get('ticket', '')
        row = self.store.run('SELECT * FROM pending WHERE ticket=?', (digest(ticket),)).fetchone()
        if not row:
            raise web.HTTPBadRequest(text='This link expired, was replaced, or sign-in already started. '
                                    'Get a fresh /osulink code in Roblox, then use /link in Discord.')
        if request.method != 'POST':
            # Link previews and scanners may GET/HEAD the URL before the player does.
            # Only an explicit form submission consumes the ticket or starts OAuth.
            nonce = secrets.token_urlsafe(32)
            response = web.Response(content_type='text/html', text=f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Link your osu! account</title></head><body>
<h1>Link your osu! account</h1>
<p>Linking Roblox user ID {html.escape(row['roblox'])}. Continue only if this is your account.</p>
<form method="post" action="/oauth/start">
<input type="hidden" name="ticket" value="{html.escape(ticket, quote=True)}">
<input type="hidden" name="nonce" value="{nonce}">
<button type="submit">Continue to osu!</button></form>
<p>After signing in, approve your osu! ID inside Roblox.</p></body></html>''')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            response.set_cookie('link_start', nonce, secure=True, httponly=True,
                                samesite='Lax', max_age=600, path='/oauth')
            return response
        nonce = form.get('nonce', '')
        if (request.headers.get('Origin') != self.base or not nonce or
                not hmac.compare_digest(nonce, request.cookies.get('link_start', ''))):
            raise web.HTTPBadRequest(text='Invalid browser session. Reopen your Discord sign-in link.')
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.store.run('UPDATE pending SET ticket=NULL, state=?, cookie=? WHERE roblox=?',
                       (digest(state), digest(cookie), row['roblox']))
        url = 'https://osu.ppy.sh/oauth/authorize?' + urlencode({
            'client_id': os.environ['OSU_CLIENT_ID'], 'redirect_uri': self.base + '/oauth/callback',
            'response_type': 'code', 'scope': 'identify', 'state': state})
        response = web.HTTPFound(url)
        response.set_cookie('link_session', cookie, secure=True, httponly=True,
                            samesite='Lax', max_age=600, path='/oauth')
        response.del_cookie('link_start', path='/oauth')
        return response

    async def callback(self, request):
        self.store.cleanup()
        state = digest(request.query.get('state', ''))
        row = self.store.run('SELECT * FROM pending WHERE state=?', (state,)).fetchone()
        if not row or not hmac.compare_digest(row['cookie'] or '', digest(request.cookies.get('link_session', ''))):
            raise web.HTTPBadRequest(text='Invalid browser session. Restart /link in Discord.')
        # Consume before any network await, preventing callback replay.
        self.store.run('UPDATE pending SET state=NULL WHERE roblox=?', (row['roblox'],))
        if 'code' not in request.query:
            raise web.HTTPBadRequest(text='Authorization cancelled. Restart /link in Discord.')
        token = await self.token(grant_type='authorization_code', code=request.query['code'],
                                 redirect_uri=self.base + '/oauth/callback')
        user = await self.osu_request('GET', '/api/v2/me',
                                     headers={'Authorization': 'Bearer ' + token['access_token']})
        # Do not persist user access or refresh tokens. Public stats use an app token.
        updated = self.store.run('UPDATE pending SET osu=?, username=?, cookie=NULL '
                                'WHERE roblox=? AND code=? AND cookie=? AND state IS NULL AND expires>?',
                       (str(user['id']), user['username'], row['roblox'], row['code'], row['cookie'], time.time()))
        if updated.rowcount != 1:
            raise web.HTTPBadRequest(text='This request expired or was replaced during sign-in. Restart /link in Discord.')
        response = web.Response(text=f"Verified osu! account {user['username']} (ID {user['id']}). "
                               f"Return to Roblox and type /osuapprove {user['id']} to finish. "
                               'Only approve if this is your own account.')
        response.del_cookie('link_session', path='/oauth')
        return response

    async def game(self, request):
        expected = 'Bearer ' + os.environ['GAME_API_KEY']
        if not hmac.compare_digest(request.headers.get('Authorization', ''), expected):
            raise web.HTTPUnauthorized()
        roblox = request.match_info['roblox']
        if not re.fullmatch(r'[1-9][0-9]{0,19}', roblox):
            raise web.HTTPBadRequest(text='Invalid Roblox ID')
        self.store.cleanup()
        action = request.match_info['action']
        self.throttle((action, roblox), 3)
        if action == 'challenge' and request.method == 'POST':
            if self.store.run('SELECT 1 FROM links WHERE roblox=?', (roblox,)).fetchone():
                raise web.HTTPConflict(text='Already linked. Use /osuunlink first.')
            code = secrets.token_hex(8).upper()
            self.store.run('DELETE FROM pending WHERE roblox=?', (roblox,))
            self.store.run('INSERT INTO pending (roblox, code, expires) VALUES (?,?,?)',
                           (roblox, digest(code), time.time() + 600))
            return web.json_response({'message': f'In Discord use /link code:{code}. Do not share this code.'})
        if action == 'approve' and request.method == 'POST':
            data = await request.json()
            try:
                self.store.approve(roblox, str(data.get('osu_id', '')))
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc))
            except sqlite3.IntegrityError:
                raise web.HTTPConflict(text='One of these accounts is already linked. Unlink it first.')
            return web.json_response({'message': 'Accounts linked successfully!'})
        if action == 'unlink' and request.method == 'POST':
            self.store.run('DELETE FROM links WHERE roblox=?', (roblox,))
            self.store.run('DELETE FROM pending WHERE roblox=?', (roblox,))
            return web.json_response({'message': 'Link removed.'})
        if action == 'stats' and request.method == 'GET':
            row = self.store.run('SELECT osu FROM links WHERE roblox=?', (roblox,)).fetchone()
            if not row:
                return web.json_response({'linked': False})
            osu = row['osu']
            async with self.stats_lock:
                now = time.monotonic()
                self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
                if osu not in self.cache:
                    if self.access_until <= now:
                        token = await self.token(grant_type='client_credentials', scope='public')
                        self.access_token = token['access_token']
                        self.access_until = now + max(0, token['expires_in'] - 60)
                    user = await self.osu_request('GET', f'/api/v2/users/{osu}/osu?key=id',
                                headers={'Authorization': 'Bearer ' + self.access_token})
                    stats = user.get('statistics') or {}
                    self.cache[osu] = (now + 300, {
                        'linked': True, 'osu_id': int(osu), 'username': user['username'],
                        'pp': stats.get('pp'), 'global_rank': stats.get('global_rank'),
                        'country_rank': stats.get('country_rank'),
                        'accuracy': stats.get('hit_accuracy'), 'play_count': stats.get('play_count')})
            # Account might have been unlinked while the upstream request was running.
            current = self.store.run('SELECT osu FROM links WHERE roblox=?', (roblox,)).fetchone()
            return web.json_response(self.cache[osu][1] if current and current['osu'] == osu else {'linked': False})
        raise web.HTTPNotFound()

    async def run(self):
        service = self

        class Bot(discord.Client):
            async def setup_hook(self):
                guild_id = os.getenv('DISCORD_GUILD_ID')
                if guild_id:
                    guild = discord.Object(id=int(guild_id))
                    tree.copy_global_to(guild=guild)
                    await tree.sync(guild=guild)
                else:
                    await tree.sync()

        bot = Bot(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        tree = app_commands.CommandTree(bot)

        @tree.command(name='link', description='Link your osu! account using a code from the Roblox game')
        async def link(interaction: discord.Interaction, code: str):
            try:
                service.throttle(('discord', str(interaction.user.id)), 5)
            except web.HTTPTooManyRequests:
                await interaction.response.send_message('Please wait a few seconds.', ephemeral=True)
                return
            service.store.cleanup()
            uid = str(interaction.user.id)
            row = service.store.run('SELECT * FROM pending WHERE code=?', (digest(code.strip().upper()),)).fetchone()
            if not row or (row['discord'] and row['discord'] != uid):
                await interaction.response.send_message('Invalid or expired code. Use /osulink inside Roblox.', ephemeral=True)
                return
            if service.store.run('SELECT 1 FROM links WHERE discord=?', (uid,)).fetchone():
                await interaction.response.send_message('You already have a link. Use /osuunlink in Roblox first.', ephemeral=True)
                return
            ticket = secrets.token_urlsafe(32)
            with service.store.db:
                service.store.db.execute('DELETE FROM pending WHERE discord=? AND roblox<>?', (uid, row['roblox']))
                service.store.db.execute('UPDATE pending SET discord=?, ticket=?, state=NULL, cookie=NULL, osu=NULL, username=NULL WHERE roblox=?',
                                         (uid, digest(ticket), row['roblox']))
            await interaction.response.send_message(
                f"Linking Roblox user ID **{row['roblox']}**. Continue only if this is your account.\n"
                f"[Sign in to osu!]({service.base}/oauth/start?ticket={ticket})\n"
                'Then approve the osu! ID inside Roblox. Never share this link.', ephemeral=True)

        @web.middleware
        async def headers(request, handler):
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = exc
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        app = web.Application(middlewares=[headers], client_max_size=2048)
        app.router.add_get('/oauth/start', self.start)
        app.router.add_post('/oauth/start', self.start)
        app.router.add_get('/oauth/callback', self.callback)
        app.router.add_route('*', '/game/{roblox}/{action}', self.game)
        app.router.add_get('/health', lambda request: web.json_response({'ok': True}))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as self.http:
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
            try:
                async with bot:
                    await bot.start(os.environ['DISCORD_TOKEN'])
            finally:
                await runner.cleanup()
                self.store.db.close()


if __name__ == '__main__':
    asyncio.run(Service().run())

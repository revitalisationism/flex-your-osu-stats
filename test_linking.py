import sqlite3
import time
import unittest
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from bot import Service, Store, digest


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.db.close()

    def pending(self, roblox='1', discord='2', osu='3', expired=False):
        self.store.run('INSERT INTO pending (roblox, code, expires, discord, osu) VALUES (?,?,?,?,?)',
                       (roblox, digest(roblox), time.time() + (-1 if expired else 600), discord, osu))

    def test_verified_link_is_single_use(self):
        self.pending()
        self.store.approve('1', '3')
        with self.assertRaises(ValueError):
            self.store.approve('1', '3')

    def test_cannot_approve_another_players_request(self):
        self.pending()
        with self.assertRaises(ValueError):
            self.store.approve('99', '3')

    def test_cannot_substitute_osu_id(self):
        self.pending()
        with self.assertRaises(ValueError):
            self.store.approve('1', '999')

    def test_unverified_account_rejected(self):
        self.pending(osu=None)
        with self.assertRaises(ValueError):
            self.store.approve('1', '3')

    def test_expired_request_rejected(self):
        self.pending(expired=True)
        with self.assertRaises(ValueError):
            self.store.approve('1', '3')

    def test_osu_account_cannot_belong_to_two_players(self):
        self.pending()
        self.store.approve('1', '3')
        self.pending('4', '5', '3')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.approve('4', '3')
        self.assertEqual(self.store.run('SELECT roblox FROM links').fetchone()['roblox'], '1')

    def test_discord_account_cannot_belong_to_two_players(self):
        self.pending()
        self.store.approve('1', '3')
        self.pending('4', '2', '6')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.approve('4', '6')


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = Service.__new__(Service)
        self.service.store = Store(':memory:')
        self.service.base = 'https://example.com'
        self.service.token = AsyncMock(return_value={'access_token': 'test'})
        self.service.osu_request = AsyncMock(return_value={'id': 3, 'username': 'Owner'})
        self.service.store.run(
            'INSERT INTO pending (roblox, code, expires, discord, state, cookie) VALUES (?,?,?,?,?,?)',
            ('1', digest('code'), time.time() + 600, '2', digest('state'), digest('cookie')))

    def tearDown(self):
        self.service.store.db.close()

    def request(self, state='state', cookie='cookie'):
        return make_mocked_request('GET', f'/oauth/callback?code=test&state={state}',
                                   headers={'Cookie': f'link_session={cookie}'})

    async def test_invalid_state_cannot_verify(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.service.callback(self.request(state='wrong'))
        self.service.token.assert_not_awaited()

    async def test_other_browser_cannot_verify(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.service.callback(self.request(cookie='wrong'))
        self.service.token.assert_not_awaited()

    async def test_callback_uses_authenticated_profile_and_cannot_replay(self):
        await self.service.callback(self.request())
        row = self.service.store.run('SELECT osu FROM pending').fetchone()
        self.assertEqual(row['osu'], '3')
        self.assertIsNone(self.service.store.run('SELECT * FROM links').fetchone())
        with self.assertRaises(web.HTTPBadRequest):
            await self.service.callback(self.request())
        self.service.token.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()

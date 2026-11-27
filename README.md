# Roblox / osu! account linking

This starter contains a Discord slash-command bot, an HTTP backend with SQLite storage,
and Roblox server/client scripts. It defaults to osu!standard statistics.

## Player flow

1. In the Roblox game, type `/osulink`. A private panel shows a code valid for ten minutes.
2. In Discord, use `/link code:THE_CODE`. The bot privately provides an osu! sign-in link.
3. Sign in on osu.ppy.sh and authorize the application. The backend reads the authenticated
   user's ID from `/api/v2/me`; entering someone else's username cannot verify their account.
4. The success page shows your osu! ID. Back in Roblox, type `/osuapprove YOUR_OSU_ID`.
5. Use `/osustats` to display PP, rank and accuracy, or `/osuunlink` to remove the mapping.

The Roblox server supplies Player.UserId. Clients never choose the Roblox ID sent to the API.
The database enforces unique Roblox, Discord and osu! IDs. Codes and OAuth tickets are hashed,
expire after ten minutes and are consumed. OAuth has state and a secure browser cookie.
Only the originating Roblox player can approve the verified osu! ID. Do not share codes,
authorization links, or approve another person's account. This proves account access; it
cannot prevent people deliberately sharing their accounts or credentials.

## Setup

1. Install Python 3.11 or later and run `python -m pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and fill in its values locally. Never send tokens in chat.
3. Create a Discord application and bot at https://discord.com/developers/applications.
   Put its bot token in DISCORD_TOKEN. Install it into your server with `bot` and
   `applications.commands` scopes. No administrator permission or privileged intents are needed.
   Set DISCORD_GUILD_ID to your test server ID for immediate guild command registration.
4. Host this process behind HTTPS on a persistent server. Set PUBLIC_URL to its public origin,
   without a trailing slash. Forward HTTPS traffic to PORT (8080 by default).
5. Register an osu! OAuth application at https://osu.ppy.sh/home/account/edit.
   Set its callback URL to `https://YOUR_HOST/oauth/callback` exactly. Copy its client ID
   and secret into OSU_CLIENT_ID and OSU_CLIENT_SECRET.
6. Generate GAME_API_KEY with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   Store it in .env and in your Roblox experience's secrets store under OSU_LINK_API_KEY.
   Restrict that secret to your backend domain. Enable Allow HTTP Requests for the experience.
7. Set API in roblox/OsuLink.server.luau to PUBLIC_URL. Add that file as a Script in
   ServerScriptService. Add OsuLink.client.luau as a LocalScript in StarterPlayerScripts.
   These scripts use TextChatService commands; players need access to Roblox text chat.
8. Run `python bot.py`. Test the full flow in the published experience; Roblox secrets
   are unavailable in ordinary local Studio playtests.

## Game integration

The server refreshes stats on join and every five minutes. Player attributes:
OsuLinked, OsuUserId, OsuPP, OsuGlobalRank, OsuCountryRank, OsuAccuracy, OsuPlayCount.
Connect your existing stat cards to these attributes. Unranked values are absent, not zero.
The supplied `/osustats` command provides a basic private display; it is not a finished UI.
Upstream failures retain the previous attributes until a successful refresh.

Authenticated endpoints: POST /game/{robloxId}/challenge, POST /game/{robloxId}/approve
with JSON `{"osu_id":"123"}`, POST /game/{robloxId}/unlink, and GET /game/{robloxId}/stats.
All require `Authorization: Bearer GAME_API_KEY`. Roblox calls them server-side only.
Public endpoints: /oauth/start, /oauth/callback, /health.

Run one process/replica with persistent storage for links.sqlite3. Back up that database;
do not deploy it on ephemeral storage. Terminate TLS at your host/reverse proxy, disable
query-string logging for /oauth paths, and apply a reasonable public request limit there.
The app disables its own HTTP access logs. User OAuth tokens are not persisted. A public
app token fetches stats, cached for five minutes. Disconnecting in Roblox removes the local
link; users can also revoke this application from their osu! account settings.

## Validation

Run `python -m unittest -v`. These tests cover the storage ownership rules, expiry,
duplicate-account rejection and one-time approval. Live Discord registration, osu! OAuth,
HTTPS hosting and Roblox HTTP calls still require your credentials and an end-to-end test.

References: https://osu.ppy.sh/docs/index.html,
https://discordpy.readthedocs.io/en/stable/interactions/api.html,
https://create.roblox.com/docs/cloud-services/secrets,
https://create.roblox.com/docs/reference/engine/classes/TextChatCommand.

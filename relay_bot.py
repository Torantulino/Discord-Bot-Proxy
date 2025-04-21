"""
Discord ↔ Platform relay bot
============================

Features
--------
* Listens for **MESSAGE_CREATE** events and forwards each non‑bot message to a user‑supplied webhook.
* Exposes a **/send** REST endpoint so your platform can post messages *back* into Discord.
* The /send endpoint is protected by a lightweight **HMAC + timestamp** scheme (shared secret). 5‑minute
  replay window; everything served over HTTPS when deployed on Railway/Fly/Render.
* Pure‑Python single file; requires only standard FastAPI/discord.py/AioHTTP stack.

Environment variables
---------------------
DISCORD_TOKEN        # Bot token from https://discord.com/developers/applications
FORWARD_WEBHOOK      # URL on your platform that will receive incoming messages (POST JSON)
RELAY_SHARED_SECRET  # 32+ char random string; both the bot and your platform must know this
PORT                 # (optional) HTTP port for FastAPI. Railway/Fly set this automatically.

Quick start
-----------
$ pip install discord.py fastapi uvicorn aiohttp python-dotenv
$ export DISCORD_TOKEN="..." FORWARD_WEBHOOK="https://example.com/webhook" \
         RELAY_SHARED_SECRET="$(openssl rand -hex 32)"
$ python relay_bot.py

On Railway: just click “Deploy”, add the three env vars, done.
"""

import os, json, time, hmac, hashlib
from threading import Thread

import aiohttp
import discord
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

# --------------------------------------------------------------------------
# Configuration & utility functions
# --------------------------------------------------------------------------
TOKEN   = os.environ["DISCORD_TOKEN"]
TARGET  = os.environ["FORWARD_WEBHOOK"]
SECRET  = os.environ["RELAY_SHARED_SECRET"]
PORT    = int(os.getenv("PORT", 8000))

if len(SECRET) < 16:
    raise RuntimeError("RELAY_SHARED_SECRET must be at least 16 characters")


def _sign(payload: bytes, ts: str) -> str:
    """Return sha256 HMAC digest as hex."""
    return hmac.new(SECRET.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()


def _verify(payload: bytes, ts: str, sig_header: str, *, leeway: int = 300) -> bool:
    """Verify HMAC signature & timestamp freshness (default ±5 min)."""
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts_int) > leeway:
        return False  # replay or clock skew
    expected = _sign(payload, ts)
    supplied = sig_header.split("=", 1)[-1]
    return hmac.compare_digest(expected, supplied)

# --------------------------------------------------------------------------
# Discord Bot (inbound → platform)
# --------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # Toggle this in Developer Portal > Bot > Privileged Gateway Intents


class RelayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.http_session = aiohttp.ClientSession()

    async def on_ready(self):
        print(f"[RelayBot] Ready as {self.user} ({self.user.id})")

    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return  # Ignore other bots (incl. itself)

        payload = {
            "guild_id": str(msg.guild.id) if msg.guild else None,
            "channel_id": str(msg.channel.id),
            "author": {
                "id": str(msg.author.id),
                "display": str(msg.author),
            },
            "content": msg.content,
            "attachments": [a.url for a in msg.attachments],
            "timestamp": msg.created_at.isoformat(),
        }
        try:
            async with self.http_session.post(TARGET, json=payload, timeout=5) as r:
                if r.status >= 400:
                    print(f"[RelayBot] Platform webhook responded {r.status}: {await r.text()}")
        except Exception as e:
            print(f"[RelayBot] Error forwarding message: {e}")

    async def close(self):
        await super().close()
        await self.http_session.close()


bot = RelayBot()

# --------------------------------------------------------------------------
# FastAPI (platform → outbound messages)
# --------------------------------------------------------------------------
app = FastAPI(title="RelayBot API", version="1.0.0", docs_url=None, redoc_url=None)


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Relay bot up and running"  # simple health‑check


@app.post("/send")
async def send(request: Request):
    ts  = request.headers.get("X-Relay-Timestamp", "")
    sig = request.headers.get("X-Relay-Signature", "")
    body = await request.body()

    if not _verify(body, ts, sig):
        raise HTTPException(401, detail="Invalid or missing signature")

    try:
        data = json.loads(body)
        channel_id = int(data["channel_id"])
        content    = data["content"]
    except Exception:
        raise HTTPException(400, detail="Payload must include 'channel_id' (int) and 'content' (str)")

    channel = bot.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, detail="Channel not found or bot lacks access")

    try:
        await channel.send(content)
    except discord.HTTPException as e:
        raise HTTPException(502, detail=f"Discord API error: {e}")

    return {"ok": True}

# --------------------------------------------------------------------------
# Run both HTTP server and Discord client in the same process
# --------------------------------------------------------------------------
def _run_api():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def main():
    Thread(target=_run_api, daemon=True).start()
    bot.run(TOKEN)


if __name__ == "__main__":
    import time
    try:
        main()
    except KeyboardInterrupt:
        print("Shutting down…")

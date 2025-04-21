"""
Discord ↔ Platform relay bot
============================

* Forwards every MESSAGE_CREATE to FORWARD_WEBHOOK.
* Exposes /send so your platform can post back into Discord.
* Secures /send with HMAC + timestamp.
* Compatible with Python 3.12+ (creates ClientSession in setup_hook).
* Uses run_coroutine_threadsafe so channel.send runs on the Discord loop.

Environment variables
---------------------
DISCORD_TOKEN        # Bot token
FORWARD_WEBHOOK      # Your platform webhook
RELAY_SHARED_SECRET  # HMAC key
PORT                 # optional; Railway injects automatically
"""

import os, json, time, hmac, hashlib, asyncio
from threading import Thread

import aiohttp
import discord
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
TOKEN   = os.environ["DISCORD_TOKEN"]
TARGET  = os.environ["FORWARD_WEBHOOK"]
SECRET  = os.environ["RELAY_SHARED_SECRET"]
PORT    = int(os.getenv("PORT", 8000))

if len(SECRET) < 16:
    raise RuntimeError("RELAY_SHARED_SECRET must be at least 16 characters")


def _sign(payload: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()


def _verify(payload: bytes, ts: str, sig_header: str, leeway: int = 300) -> bool:
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts_int) > leeway:
        return False
    expected = _sign(payload, ts)
    supplied = sig_header.split("=", 1)[-1]
    return hmac.compare_digest(expected, supplied)

# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # toggle in Developer Portal

class RelayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()

    async def on_ready(self):
        print(f"[RelayBot] Ready as {self.user} ({self.user.id})")

    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return
        payload = {
            "guild_id": str(msg.guild.id) if msg.guild else None,
            "channel_id": str(msg.channel.id),
            "author": {"id": str(msg.author.id), "display": str(msg.author)},
            "content": msg.content,
            "attachments": [a.url for a in msg.attachments],
            "timestamp": msg.created_at.isoformat(),
        }
        try:
            async with self.http_session.post(TARGET, json=payload, timeout=5) as r:
                if r.status >= 400:
                    print(f"[RelayBot] Webhook responded {r.status}: {await r.text()}")
        except Exception as e:
            print(f"[RelayBot] Error forwarding message: {e}")

    async def close(self):
        await super().close()
        if self.http_session:
            await self.http_session.close()

bot = RelayBot()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="RelayBot API", docs_url=None, redoc_url=None)

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Relay bot up and running"

@app.post("/send")
async def send(request: Request):
    # Signature verification
    ts  = request.headers.get("X-Relay-Timestamp", "")
    sig = request.headers.get("X-Relay-Signature", "")
    body = await request.body()
    if not _verify(body, ts, sig):
        raise HTTPException(401, detail="Invalid or missing signature")

    # Parse payload
    try:
        data = json.loads(body)
        channel_id = int(data["channel_id"])
        content    = data["content"]
    except Exception:
        raise HTTPException(400, detail="Payload must include 'channel_id' (int) and 'content' (str)")

    channel = bot.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, detail="Channel not found or bot lacks access")

    # Dispatch send on the Discord loop
    fut = asyncio.run_coroutine_threadsafe(channel.send(content), bot.loop)
    try:
        await asyncio.wrap_future(fut)
    except Exception as e:
        raise HTTPException(502, detail=f"Discord API error: {e}")

    return {"ok": True}

# ---------------------------------------------------------------------------
# Run FastAPI + Discord
# ---------------------------------------------------------------------------
def _run_api():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

def main():
    Thread(target=_run_api, daemon=True).start()
    bot.run(TOKEN)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Shutting down…")

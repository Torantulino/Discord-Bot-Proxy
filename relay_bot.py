"""
Discord ↔ Platform relay bot
============================

* Listens for MESSAGE_CREATE and forwards messages to FORWARD_WEBHOOK.
* Exposes /send (FastAPI) so your platform can post back to Discord.
* /send is secured with HMAC + timestamp (shared secret, 5‑minute window).
* Runs on Python 3.12+ — updated so aiohttp.ClientSession is created only
  after the event loop starts (fixes RuntimeError: no running event loop).

Environment variables
---------------------
DISCORD_TOKEN        # Bot token from https://discord.com/developers/applications
FORWARD_WEBHOOK      # URL on your platform that will receive incoming messages
RELAY_SHARED_SECRET  # 32‑char+ random string for HMAC
PORT                 # (optional) override; Railway injects it automatically
"""

import os, json, time, hmac, hashlib
from threading import Thread

import aiohttp
import discord
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
TOKEN   = os.environ["DISCORD_TOKEN"]
TARGET  = os.environ["FORWARD_WEBHOOK"]
SECRET  = os.environ["RELAY_SHARED_SECRET"]
PORT    = int(os.getenv("PORT", 8000))

if len(SECRET) < 16:
    raise RuntimeError("RELAY_SHARED_SECRET must be at least 16 characters")


def _sign(payload: bytes, ts: str) -> str:
    return hmac.new(SECRET.encode(), ts.encode() + b"." + payload, hashlib.sha256).hexdigest()


def _verify(payload: bytes, ts: str, sig_header: str, *, leeway: int = 300) -> bool:
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
# Discord Bot
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # toggle in Developer Portal > Bot > Privileged Gateway Intents


class RelayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.http_session: aiohttp.ClientSession | None = None  # set in setup_hook

    # runs after the event loop is up but before on_ready
    async def setup_hook(self) -> None:
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()

    async def on_ready(self):
        print(f"[RelayBot] Ready as {self.user} ({self.user.id})")

    async def on_message(self, msg: discord.Message):
        if msg.author.bot:
            return  # ignore bots (incl. itself)

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
                    print(f"[RelayBot] Webhook responded {r.status}: {await r.text()}")
        except Exception as e:
            print(f"[RelayBot] Error forwarding message: {e}")

    async def close(self):
        await super().close()
        if self.http_session:
            await self.http_session.close()


bot = RelayBot()

# ---------------------------------------------------------------------------
# FastAPI app (platform → Discord)
# ---------------------------------------------------------------------------
app = FastAPI(title="RelayBot API", docs_url=None, redoc_url=None)


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Relay bot up and running"


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

# ---------------------------------------------------------------------------
# Run FastAPI (thread) + Discord client (main thread)
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

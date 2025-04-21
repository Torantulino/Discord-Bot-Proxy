# Discord ↔ Platform **Relay Bot**

A one‑file, open‑source proxy that forwards every new Discord message to **your** webhook and lets your platform post messages back into Discord via a simple, HMAC‑secured REST endpoint.

---

## ✨ Features
* **Listen**: Subscribes to `MESSAGE_CREATE`, filters out bot traffic, and `POST`s a clean JSON payload to `FORWARD_WEBHOOK`.
* **Talk back**: `/send` endpoint accepts `{channel_id, content}` and posts into that channel.
* **Secure by default**: Shared‑secret HMAC (`X‑Relay‑Signature`) + 5‑minute replay window.
* **Zero ops**: Deploy to [Railway](https://railway.app) in one click, free tier friendly (<100 MB RAM).

---

## 🚀 One‑click deploy (Railway)
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?templateRepo=https://github.com/Torantulino/Discord-Bot-Proxy)

1. Click the button above.  
2. Add these **environment variables** when prompted:

   | Key | Example | Notes |
   |-----|---------|-------|
   | `DISCORD_TOKEN` | `MTEx…t.XYZ` | From *Bot → Reset Token* in the Discord Developer Portal. |
   | `FORWARD_WEBHOOK` | `https://platform.example.com/webhook/abc` | Your platform’s incoming webhook URL. |
   | `RELAY_SHARED_SECRET` | `6f7f3d…` | 32‑byte random hex; generate with `openssl rand -hex 32`. |

3. Hit **Deploy**. Railway builds, runs, and assigns a public HTTPS URL (e.g. `https://relay-production.up.railway.app`).  
4. Copy that URL; your platform will use `https://…/send` for outbound messages.

> **Privileged intent:** In the Discord Developer Portal, toggle **Message Content Intent** (Bot → Privileged Gateway Intents) or the bot cannot read message bodies.

---

## 🖥️ Local development
~~~bash
git clone https://github.com/Torantulino/Discord-Bot-Proxy
cd discord-relay-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit the three vars
python relay_bot.py    # FastAPI on :8000, bot logs to stdout
~~~

### Signing helper (Python)
~~~python
import hmac, hashlib, time, requests, os, json
SECRET = os.getenv("RELAY_SHARED_SECRET")
body   = json.dumps({"channel_id": 1234567890, "content": "hello world"}).encode()
ts     = str(int(time.time()))
sig    = "sha256=" + hmac.new(SECRET.encode(), ts.encode()+b"."+body, hashlib.sha256).hexdigest()
headers = {
    "X-Relay-Timestamp": ts,
    "X-Relay-Signature": sig,
    "Content-Type": "application/json",
}
requests.post("https://YOUR-RAILWAY-URL/send", headers=headers, data=body)
~~~

---

## 📂 Repository layout
```
.
├── relay_bot.py        # the proxy bot (see inline docstring)
├── requirements.txt    # Python deps pinned loosely
├── railway.json        # Railway build + run config
├── .gitignore          # excludes virtualenvs, IDE junk, .env
└── README.md           # you are here
```

### requirements.txt
~~~text
discord.py>=2.3.2
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
aiohttp>=3.9.3
python-dotenv>=1.0.1
~~~

### railway.json
~~~json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python relay_bot.py",
    "healthcheckPath": "/"
  }
}
~~~

---

## 🔒 Security notes
* All traffic must be HTTPS (Railway handles TLS).
* HMAC secret rotates just like any API key. Maintain two keys during cut‑over:
  1. Add `RELAY_SHARED_SECRET_OLD`, accept either digest.
  2. Update platform, wait, then remove the old variable.
* Discord rate limits: 50 messages/second per bot; if you approach this, add an internal queue and exponential back‑off.

---

## 🛠️ Discord bot setup recap
1. Create an **Application** → **Bot** → *Add Bot*.
2. Copy the token into `DISCORD_TOKEN` (keep it secret!).
3. Under *Privileged Gateway Intents* tick **Message Content Intent**.
4. *OAuth2 → URL Generator*: select scope `bot`, permissions `Read Messages/View Channels` + `Send Messages`.
5. Paste the invite URL into a browser, pick your server.

---

## 📜 License
MIT — see `LICENSE` for details. Commercial use welcome; attribution appreciated.

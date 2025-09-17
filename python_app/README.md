Peplink Hardware Finder — Python (Flask)

A minimal Flask web app that mirrors the static MVP in this repo, letting sales quickly click through categories and attribute chips to find matching hardware, with one-click PDF access.

Key points
- Reads data from `../data/hardware_data.json` by default.
- No manual typing needed — all selections are made by clicking chips/links.
- Stateless URLs: selections persist in query parameters, so you can refresh or share.
- Quick picks: for Mobile Routers, jump to Single/Multi modem and 5G.
- Range filters: filter by min Router Throughput (Mbps), min SpeedFusion (Mbps), and min Recommended Users.
- Sorting: sort by Name, Router Throughput, SpeedFusion, or Users.

Run locally
1) Create a virtualenv (optional but recommended)
   - `python3 -m venv .venv && source .venv/bin/activate`
2) Install Flask
   - `pip install flask`
3) Start the app from this folder
   - `FLASK_APP=app.py flask run` (or `python app.py`)
4) Open
   - http://127.0.0.1:5000/

Notes
- If you move this folder, update `DATA_PATH` in `app.py` if needed.
- The UI styling is copied from the static MVP for consistency.
- Attribute order is curated to emphasize core specs (modems, 5G, Wi‑Fi, ports, throughput), then auto-ranked.

Deploy (Option A — Subdomain + iFrame)
- Overview: host at `finder.peplink.com` with TLS via Caddy, then embed in a WordPress page as an iframe.

1) DNS
   - Create an A/AAAA record for `finder.peplink.com` to your server’s IP.

2) Build & run with Docker Compose
   - `docker compose build`
   - Set domain/email in `docker-compose.yml` (env for Caddy) or export:  
     `export DOMAIN=finder.peplink.com EMAIL=your@peplink.com`
   - `docker compose up -d`
   - Verify: `curl -I https://finder.peplink.com/health`

3) Embed in peplink.com (WordPress)
   - Add a page under Tools or Sales and insert a Custom HTML block:
     `<iframe src="https://finder.peplink.com/?embed=1" style="width:100%;min-height:80vh;border:0" loading="lazy"></iframe>`
   - Preselect examples:
     - Mobile Routers: `https://finder.peplink.com/?embed=1&category=Mobile%20Routers`
     - 5G only: `...?embed=1&category=Mobile%20Routers&5G%20support=Yes`

4) Data updates
   - The image bakes in `data/hardware_data.json`. To update without rebuild, bind mount:
     - Edit `docker-compose.yml` service `app`:
       `volumes: ["./data/hardware_data.json:/app/data/hardware_data.json:ro"]`
   - Or rebuild: `docker compose build && docker compose up -d`

5) Access control (optional)
   - Keep public, or restrict the WordPress page to authenticated users. Caddy can also add Basic Auth if desired.

6) Troubleshooting
   - If the iframe is blocked, ensure no proxy in front adds `X-Frame-Options: DENY` and CSP allows `frame-ancestors` for `https://*.peplink.com`.
   - Health: `GET /health` returns `{status:"ok"}` from the app and is also used by the container healthcheck.

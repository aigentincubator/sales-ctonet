Option A — Subdomain + iFrame on peplink.com

Goal
- Host the app at a public subdomain (e.g., finder.peplink.com) and embed it in a WordPress page on peplink.com for sales use.

Prereqs
- Server/VM with Docker and Docker Compose plugin.
- Ports 80/443 open to the internet (Let’s Encrypt needs 80).
- DNS A/AAAA record for finder.peplink.com pointing to the server IP.

Deploy Steps
- SSH to the server and clone the repo, then in the repo root:
  - (Optional) export variables for your domain/email:  
    DOMAIN=finder.peplink.com EMAIL=your@peplink.com ./deploy/up.sh
  - Or run with defaults:  
    ./deploy/up.sh
  - Verify:  
    curl -I https://finder.peplink.com/health  → 200 OK

Embed on peplink.com (WordPress)
- Add a page and insert a Custom HTML block with:
  <iframe src="https://finder.peplink.com/?embed=1" style="width:100%;min-height:80vh;border:0" loading="lazy"></iframe>

Deep-link examples for sales
- Mobile Routers:
  https://finder.peplink.com/?embed=1&category=Mobile%20Routers
- 5G filter:
  https://finder.peplink.com/?embed=1&category=Mobile%20Routers&5G%20support=Yes

Update data
- Rebuild image with new JSON (baked-in):
  docker compose build && docker compose up -d
- Or bind-mount the JSON so updates don’t require rebuild: add this under service `app` in docker-compose.yml:
  volumes:
    - ./data/hardware_data.json:/app/data/hardware_data.json:ro

Stop/Remove stack
- ./deploy/down.sh

Alternative: Nginx instead of Caddy
- Use deploy/nginx-peplink-subdomain.conf as a starting point. Ensure TLS is handled (Certbot or your existing certs) and add the same CSP headers to allow iframe embedding from peplink.com.


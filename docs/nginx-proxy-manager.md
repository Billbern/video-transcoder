# Nginx Proxy Manager setup

This project does **not** ship its own reverse-proxy container. TLS, HTTP→HTTPS
redirection, and Lets Encrypt are all handled by **Nginx Proxy Manager (NPM)**,
which runs as a separate stack on the Hetzner VPS and serves multiple apps
(so they share one set of public ports 80/443).

## Why NPM

- One web UI for the whole VPS, not one reverse-proxy config per app.
- Lets Encrypt via DNS-01 or HTTP-01, auto-renewed.
- Access lists and basic auth at the proxy level (useful for staging).
- Survives app restarts (NPM is decoupled from `docker compose`).

## Architecture

```
Internet
   |
   v
Nginx Proxy Manager (:80, :443)  <-- runs in its own compose project
   |
   |  docker network: videotranscoder
   v
frontend (Sprint 3)  --SPA static files--
   |
   |  /api/*  /docs  /openapi.json  ----+
   |                                    |
   +-> backend (:8000)  --FastAPI-- <--+
   |
   +-> minio (:9001)   --admin UI--
        (optional second Proxy Host)
```

NPM and the app containers share the `videotranscoder` Docker network
(declared in `docker-compose.yml`).

## One-time setup on the Hetzner VPS

### 1. Install NPM (if not already running)

NPM ships a single Docker image. Create its own directory:

```bash
mkdir -p ~/nginx-proxy-manager && cd ~/nginx-proxy-manager
cat > docker-compose.yml <<'YAML'
services:
  npm:
    image: jc21/nginx-proxy-manager:latest
    container_name: npm-app
    restart: unless-stopped
    ports:
      - "80:80"     # HTTP
      - "443:443"   # HTTPS
      - "81:81"     # Admin UI
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
YAML
docker compose up -d
```

The admin UI is at `http://<vps-ip>:81`. Default credentials:
`admin@example.com` / `changeme` (change on first login).

### 2. Point DNS at the VPS

In your DNS provider, create an A (or AAAA) record:

| Name        | Type | Value       |
| ----------- | ---- | ----------- |
| `videotranscode.example.com` | A    | `<vps-ipv4>` |

(Use a wildcard `*.example.com` if you want staging under the same domain.)

### 3. Attach NPM to the app network

```bash
# Find the NPM container name
docker ps --format '{{.Names}}' | grep npm   # probably `npm-app`

# Bring up the app stack (creates the `videotranscoder` network)
cd ~/video-transcoder   # or wherever you cloned the repo
docker compose pull
docker compose up -d

# Wire NPM into the app network
docker network connect videotranscoder npm-app
```

Verify with:

```bash
docker network inspect videotranscoder \
  | jq '.[0].Containers[].Name'   # should list "npm-app" + app services
```

### 4. Add a Proxy Host in the NPM UI

1. Open `http://<vps-ip>:81` and log in.
2. **Hosts -> Proxy Hosts -> Add Proxy Host**:
   - **Domain Names**: `videotranscode.example.com`
   - **Scheme**: `http`
   - **Forward Hostname / IP**: `frontend`
   - **Forward Port**: `80`
   - **Block Common Exploits**: ON
   - **Websockets Support**: ON
3. **SSL** tab:
   - **SSL Certificate**: `Request a new SSL Certificate`
   - **Force SSL**: ON
   - **HTTP/2 Support**: ON
   - **HSTS Enabled**: ON (optional but recommended)
   - Agree to the LE TOS and save.

NPM will issue the cert and renew it automatically. The app is now served at
`https://videotranscode.example.com`.

### 5. (Optional) Expose the MinIO console

NPM's "Access Lists" can be combined with a second Proxy Host. The MinIO
console is admin-only, so put it behind basic auth:

- **Domain Names**: `minio.example.com`
- **Forward Hostname / IP**: `minio`
- **Forward Port**: `9001`
- **Access List**: `Admins` (with at least one user)

If you don't need the admin UI publicly, you can SSH-tunnel instead:
`ssh -L 9001:minio:9001 user@<vps>` and browse to `http://localhost:9001`.

### 6. (Optional) `https://api.videotranscode.example.com`

If you want the API to have its own subdomain (e.g., for curl scripts or
external clients), add a second Proxy Host:

- **Domain Names**: `api.videotranscode.example.com`
- **Forward Hostname / IP**: `backend`
- **Forward Port**: `8000`
- **SSL**: same as above (Lets Encrypt via NPM)

Remember to set `CORS_ORIGINS=https://videotranscode.example.com` in the
backend `.env` so browsers allow cross-origin requests.

## Updating the app

```bash
cd ~/video-transcoder
git pull
docker compose pull
docker compose up -d
```

NPM does not need to be touched. Its `frontend` / `backend` hostnames resolve
via Docker's embedded DNS, so rolling containers keeps the proxy live.

## Backing up NPM

`./data` (the sqlite DB) and `./letsencrypt` (the certs) are the only
stateful bits. Snapshot them however you snapshot the rest of the VPS
(e.g., `restic`, Hetzner snapshots, etc.).

## Troubleshooting

| Symptom                                      | Likely cause                                    | Fix                                              |
| -------------------------------------------- | ----------------------------------------------- | ------------------------------------------------ |
| 502 Bad Gateway in NPM                       | NPM isn't on the `videotranscoder` network      | `docker network connect videotranscoder npm-app`  |
| Lets Encrypt fails                           | DNS not pointing at VPS yet                     | Wait for TTL, then retry from NPM UI             |
| CORS errors in the browser                   | `CORS_ORIGINS` doesn't include your domain      | Update `.env`, `docker compose restart backend`   |
| 413 / upload too large via HTTPS             | NPM default client body size is 50 MB           | Custom `client_max_body_size` per Proxy Host      |
| Slow first request after backend restart     | Cold start; FFmpeg is loading                   | Add a small healthcheck warm-up in `backend.Dockerfile` |

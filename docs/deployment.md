### 📄 DOCUMENT 9: Deployment Strategy

**Target**: Hetzner Cloud (Cost-optimized, high performance).

*   **Environment 1: Local Development**
    *   `docker-compose.dev.yml`: Runs app with hot-reloading, mounts local code, exposes DB ports to localhost.
*   **Environment 2: Staging/Production (Hetzner VPS)**
    *   *Infrastructure*: A single Hetzner Cloud VPS (e.g., CPX21 - 3 vCPU, 4GB RAM is plenty for V1).
    *   *Orchestration*: Docker Compose in production mode. (Skip Kubernetes for V1; K8s on a single VPS is over-engineering and adds massive operational overhead).
    *   *Reverse Proxy*: Nginx Proxy Manager (NPM) — runs as a separate stack, joins the `videotranscoder` Docker network, and issues / renews Let's Encrypt certificates via its web UI. See `nginx-proxy-manager.md` for setup.
    *   *CI/CD*: GitHub Actions. On push to `main`, it builds the Docker images, pushes them to GitHub Container Registry (GHCR), SSHs into the Hetzner server, pulls the new images, and runs `docker compose up -d`.

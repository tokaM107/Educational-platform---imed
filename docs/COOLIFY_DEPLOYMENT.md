# Coolify deployment: internal FastAPI service

This application is designed to run as an internal-only Dockerfile application
on the same Coolify server/network as its caller. Do not assign a domain and do
not add a host port mapping.

## Create the application

1. In the intended Coolify project/environment, create an **Application** from
   the Git repository and select the production branch/commit.
2. Choose the **Dockerfile** build pack.
3. Set Base Directory to `/` and Dockerfile Location to `/Dockerfile` (both are
   repository-relative).
4. Set **Ports Exposes** to `8000`. Leave **Port Mappings** empty.
5. Leave **Domains** empty and disable any automatically generated domain.
6. Select the same server and destination network as the calling NestJS
   application. The standard destination is the shared `coolify` network.
7. In Advanced settings, use the `unless-stopped` restart policy. Keep one
   application instance/worker initially.
8. The image defines its own `/health` Docker health check, so do not add a
   conflicting dashboard health check. Coolify should detect the image check.

`EXPOSE 8000` is image metadata; it does not publish the port on the Ubuntu
host. A public route exists only if a domain or host port mapping is added.

## Environment variables

Add values through **Configuration > Environment Variables**. Never upload the
local `.env` file.

Required for import/startup:

- `DATABASE_URL`
- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`

Required for the complete current feature set:

- `GEMINI_API_KEY` for AI chat, search, embeddings, and narratives
- `SUPABASE_SECRET_KEY` for password-reset/session administration
- `BUNNY_STREAM_LIBRARY_ID`
- `BUNNY_STREAM_API_KEY`
- `BUNNY_STREAM_CDN_HOSTNAME`

Set these production controls explicitly:

```text
ENABLE_DEMO_UI=false
ENFORCE_SUBSCRIPTIONS=true
LOG_LEVEL=INFO
REPORT_TIMEZONE=Africa/Cairo
```

Use `.env.example` as the complete safe template. `JWT_SECRET`,
`JWT_ALGORITHM`, `ACCESS_TOKEN_MINUTES`, `SUPABASE_DATABASE_URL`,
`SUPABASE_JWKS_URL`, and `HF_TOKEN` are not consumed by this HTTP runtime.

## Private connection from NestJS

Both containers must run on the same server and share the same Docker network.
A normal non-Compose Coolify application uses its selected destination network;
a Compose/Service caller may need **Connect To Predefined Network** enabled to
join that destination.

In Coolify, inspect the FastAPI application's **Network Aliases** or its actual
container name after deployment. Verify that name from the NestJS container;
do not assume the application display name is the Docker hostname. Configure
NestJS with:

```text
FASTAPI_BASE_URL=http://<verified-network-alias-or-container-name>:8000
```

No domain, public IP, or host-mapped port is involved.

## Verify and view the service

Because the API is private, “viewing it on the server” means using Coolify's
Terminal from a container on the shared network:

```sh
python -c "import urllib.request; print(urllib.request.urlopen('http://<verified-network-alias-or-container-name>:8000/health').read().decode())"
```

Expected response:

```json
{"status":"ok"}
```

You can also open the FastAPI application's own Terminal and run the same check
against `http://127.0.0.1:8000/health`. There is intentionally no browser URL.

Use the application's **Logs** page for Uvicorn stdout/stderr. Use
**Deployments**, then open a deployment, for clone/build/start logs and the
exact deployed revision. If server SSH access is separately authorized, the
equivalent operational checks are `docker ps`, `docker inspect`, and
`docker logs <verified-container-name>`; resolve the container name first.

## Deploy, restart, and rollback

After saving configuration, press **Deploy** and wait for the deployment and
health check to succeed. Environment or network changes require a restart or
redeploy so Coolify recreates the container.

For rollback, open **Deployments**, select the previous successful deployment
whose image is still available locally, and choose **Rollback**. Confirm its
health check before investigating the failed revision. Coolify can only roll
back to an image still present on the deployment server, so avoid manually
pruning the last known-good image during an incident.

## Database ownership

This container must not run migrations automatically. The separate Supabase
database source-of-truth repository owns schema migrations. Apply and verify
database changes through that repository's process before deploying API code
that depends on them.

References: [Coolify application configuration](https://next.coolify.io/docs/applications/configuration/general),
[networking](https://next.coolify.io/docs/core/networking-in-coolify),
[health checks](https://next.coolify.io/docs/applications/configuration/health-checks),
and [deployments](https://next.coolify.io/docs/applications/deployments/overview).

# ccx-site

Landing page + docs for [ccx](../README.md), built with **Astro 5** + **Tailwind v4**.
Static output, served by nginx in Docker.

## Develop

```bash
cd site
npm install
npm run dev          # http://localhost:4321
```

## Build (static)

```bash
npm run build        # outputs to dist/
npm run preview      # serve the built site locally
```

## Deploy with Docker (VPS)

```bash
cd site
docker compose up -d --build
```

Serves on host port **8473** (→ container 80). Put it behind your reverse proxy /
TLS terminator and point a domain at it. Update `site` in `astro.config.mjs` to your
real domain for correct canonical URLs.

```bash
docker compose down          # stop
docker compose up -d --build # rebuild after changes
```

## Structure

```
src/
  layouts/    Base.astro, Docs.astro
  components/ Nav, Hero, Features, ProviderGrid, CodeBlock, Footer
  pages/
    index.astro            landing
    docs/*.md               documentation pages
  styles/global.css        Tailwind import + dark theme
public/favicon.svg
Dockerfile · docker-compose.yml · nginx.conf
```

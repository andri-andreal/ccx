# Landing site — design

Date: 2026-06-27

## Goal

A marketing landing page plus documentation pages for `ccx`
(claude-code-profile-switcher), self-hosted by the user on their own VPS via
Docker.

## Stack

- **Astro 5**, static output (`output: 'static'`).
- **Tailwind v4** via the `@tailwindcss/vite` plugin.
- Dark, terminal/developer-tool aesthetic; monospace accents.
- Served by **nginx** inside a multi-stage Docker image.

## Location & structure

Astro project lives in `site/` inside the repo:

```
site/
  astro.config.mjs
  package.json
  tsconfig.json
  src/
    styles/global.css        # Tailwind import + theme tokens
    layouts/
      Base.astro             # html shell, nav + footer, meta
      Docs.astro             # docs shell with sidebar (Markdown layout)
    components/
      Nav.astro
      Hero.astro
      Features.astro
      ProviderGrid.astro
      CodeBlock.astro        # command block with copy button
      Footer.astro
    pages/
      index.astro            # landing
      docs/
        index.astro          # docs hub
        installation.md
        usage.md
        providers.md
        desktop-gui.md
        roadmap.md
  Dockerfile                 # node build -> nginx serve
  docker-compose.yml         # 8473:80
  nginx.conf
  .dockerignore
```

## Landing page sections

Hero (tagline + `./install.sh` + GitHub button) → Features → Supported providers
→ Quick start (`ccx` commands) → GUI preview → Roadmap teaser → Footer. Content
mirrors the repo README.

## Docs

Markdown pages under `src/pages/docs/`, each using `Docs.astro` as its
`layout` frontmatter. The docs layout renders a fixed sidebar listing:
Installation, Usage, Providers, Desktop GUI, Roadmap. `docs/index.astro` is a
short hub linking to them.

## Docker

Multi-stage:
1. `node:22-alpine` — `npm ci` + `npm run build` → `dist/`.
2. `nginx:alpine` — copy `dist/` to web root, custom `nginx.conf` (gzip, sane
   cache headers, `try_files` for clean 404s).

`docker-compose.yml` maps host **8473** → container 80. Deploy on the VPS with
`docker compose up -d --build`.

## Out of scope (YAGNI)

SSR, CMS, i18n, analytics, blog, search. Can be added later.

## Verification

`npm run build` succeeds and `docker build` produces a runnable image serving the
landing + docs pages.

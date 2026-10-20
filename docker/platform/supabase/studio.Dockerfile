# To be run in the root of the turbo monorepo
# NOTE: It's highly recommended to use the new builder, Buildkit. https://docs.docker.com/build/buildkit/
## USAGE:
# Build (Next):     docker build . -f apps/studio/Dockerfile --target production -t studio:latest
# Build (TanStack): docker build . -f apps/studio/Dockerfile --target production -t studio:latest --build-arg STUDIO_FRAMEWORK=tanstack
# Run:          docker run -p 3000:3000 supabase/studio
# Deploy:       docker push supabase/studio:latest
# Clean build:
#    docker builder prune
#    docker build . -f apps/studio/Dockerfile --target production -t studio:latest --no-cache

# Which framework's build ends up in the image. This is the same variable
# scripts/dispatch.js keys on for the dev/build/start scripts, so
# `--build-arg STUDIO_FRAMEWORK=tanstack` is the docker spelling of the
# switch used everywhere else. Framework selection happens at image build
# time (the two runtimes need different build outputs and dependency
# trees), not at container start.
ARG STUDIO_FRAMEWORK=next

FROM node:22-slim AS base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

# Fixes issues with Sentry CLI and SSL certificates during build
# TODO: Git is added because it's needed to build libpg, remove it once they publish a binary on the S3 bucket
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update -qq && \
  apt-get install -y --no-install-recommends \
  git \
  python3 \
  ca-certificates \
  build-essential && \
  rm -rf /var/lib/apt/lists/* && \
  update-ca-certificates

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm install -g pnpm@11.13.1

WORKDIR /app

# Prune unneeded dependencies with turbo (from apps/ for example)
FROM base AS turbo
COPY . .

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm dlx turbo@2.9.14 prune studio --docker

# Install dev dependencies (only if needed)
FROM base AS deps
COPY --from=turbo /app/out/json ./
COPY --from=turbo /app/out/pnpm-lock.yaml ./
COPY ./patches/ ./patches

# No need to clean cache because production uses standalone build
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm install --frozen-lockfile

# dev contains dependencies and source code not compiled
FROM deps AS dev
COPY --from=turbo /app/out/full ./
ENTRYPOINT ["docker-entrypoint.sh"]
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; python3 -c 'exec('"'"'"""仅在镜像构建目录修复锁定 Studio 源码中的根路径假设。"""\nfrom pathlib import Path\n\n\ndef replace(relative, old, new, count):\n    path = Path("apps/studio") / relative\n    text = path.read_text()\n    assert text.count(old) == count, f"上游已变更，请重新审核目录补丁：{relative}"\n    path.write_text(text.replace(old, new))\n\n\nreplace("lib/constants/index.ts", "return \'"'"'/api\'"'"'",\n        "return (process.env.NEXT_PUBLIC_BASE_PATH ?? \'"'"'\'"'"') + \'"'"'/api\'"'"'", 2)\nreplace("lib/constants/api.ts", "${PUBLIC_URL.origin}/rest/v1/",\n        "${PUBLIC_URL.origin}${PUBLIC_URL.pathname.replace(/\\\\/$/, \'"'"'\'"'"')}/rest/v1/", 1)\nreplace("lib/constants/api.ts", "PROJECT_ENDPOINT = PUBLIC_URL.host",\n        "PROJECT_ENDPOINT = PUBLIC_URL.host + PUBLIC_URL.pathname.replace(/\\\\/$/, \'"'"'\'"'"')", 1)\nfor name, variable in (("public-url", "publicUrl"), ("sign", "signedUrl"), ("sign-multi", "signedUrl")):\n    replace(f"pages/api/platform/storage/[ref]/buckets/[id]/objects/{name}.ts",\n            f"{variable}.port = parsed.port",\n            f"{variable}.port = parsed.port\\n  {variable}.pathname = parsed.pathname.replace(/\\\\/$/, \'"'"'\'"'"') + {variable}.pathname", 1)\n'"'"')'
EXPOSE 8082
CMD ["pnpm", "dev:studio"]

# Compile Next.js
FROM dev AS build-next
ARG NEXT_PUBLIC_BASE_PATH=/supabase
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm --filter studio exec next build

# Assemble the runtime tree at /srv in the layout the production stage
# serves from: Next's self-contained standalone output is the app root,
# with the static assets and public/ laid alongside it.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /srv && \
  cp -a apps/studio/.next/standalone/. /srv/ && \
  mkdir -p /srv/apps/studio/.next && \
  cp -a apps/studio/.next/static /srv/apps/studio/.next/static && \
  cp -a apps/studio/public /srv/apps/studio/public

# Compile TanStack Start (Vite + Nitro)
FROM dev AS build-tanstack
ARG NEXT_PUBLIC_BASE_PATH=/supabase
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; NODE_OPTIONS=--max-old-space-size=4096 pnpm --filter studio run build:tanstack

# Assemble the runtime tree at /srv. Nitro's node-server output is
# self-contained (`.output/server` bundles the app with its traced
# dependencies, `.output/public` holds the client assets), so no node_modules
# install is needed. The server.js shim loads `.env` (container env vars win)
# and gives the production stage one CMD for both frameworks; package.json is
# copied so the shim runs as ESM.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /srv/apps/studio && \
  cp -a apps/studio/.output /srv/apps/studio/.output && \
  cp apps/studio/package.json apps/studio/.env /srv/apps/studio/ && \
  printf "process.loadEnvFile(new URL('.env', import.meta.url))\nawait import('./.output/server/index.mjs')\n" > /srv/apps/studio/server.js

# Alias whichever framework build was selected so the production stage can
# COPY from a single stage name. BuildKit only builds the selected branch.
FROM build-${STUDIO_FRAMEWORK} AS build

# Copy only compiled code and dependencies
FROM base AS production
COPY --from=build /srv ./
# Both servers read PORT (the TanStack `start` script defaults it to 8082);
# pin it to the port the healthcheck and compose files expect.
ENV PORT=3000
EXPOSE 3000
ENTRYPOINT ["docker-entrypoint.sh"]
HEALTHCHECK --interval=5s --timeout=5s --retries=3 CMD node -e "fetch('http://localhost:3000/supabase/api/platform/profile').then((r) => {if (r.status !== 200) throw new Error(r.status)})"
CMD ["node", "apps/studio/server.js"]

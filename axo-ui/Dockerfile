# axo-ui (the people's app), built from the repo root: the pnpm workspace
# (axo-ui + the axo-ui-kit layer) and its one lockfile live there.
#   docker build -f axo-ui/Dockerfile -t axo-ui:local .
# The browser calls the Control Plane at NUXT_PUBLIC_API_BASE (read at
# start, so one image serves any address).
FROM node:24-alpine AS build
RUN corepack enable && corepack prepare pnpm@11.5.2 --activate
WORKDIR /web
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY axo-ui-kit/ ./axo-ui-kit/
COPY axo-ui/ ./axo-ui/
RUN pnpm install --frozen-lockfile --filter axo-ui... \
    && pnpm --filter axo-ui build

FROM node:24-alpine
WORKDIR /app
COPY --from=build /web/axo-ui/.output ./.output
ENV HOST=0.0.0.0 PORT=3000 NODE_ENV=production
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]

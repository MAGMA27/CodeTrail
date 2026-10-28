FROM node:24-bookworm-slim AS build
WORKDIR /workspace
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm --dir apps/web build

FROM node:24-bookworm-slim
WORKDIR /workspace
ENV NODE_ENV=production
COPY --from=build /workspace/apps/web/.next apps/web/.next
COPY --from=build /workspace/apps/web/package.json apps/web/package.json
COPY --from=build /workspace/apps/web/node_modules apps/web/node_modules
COPY --from=build /workspace/node_modules node_modules
EXPOSE 3000
CMD ["apps/web/node_modules/.bin/next", "start", "apps/web", "--hostname", "0.0.0.0"]

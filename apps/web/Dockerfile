# SUFFICIA web app: Next.js standalone server. SUFFICIA_API_URL is read at runtime by the /api/v1 proxy,
# so the same image works against any API (docker compose sets it to http://api:8000).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000 \
    SUFFICIA_API_URL=http://api:8000
RUN addgroup -S -g 10001 sufficia && adduser -S -u 10001 -G sufficia sufficia
COPY --from=build --chown=sufficia:sufficia /app/.next/standalone ./
COPY --from=build --chown=sufficia:sufficia /app/.next/static ./.next/static
COPY --from=build --chown=sufficia:sufficia /app/public ./public
USER sufficia
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"
CMD ["node", "server.js"]

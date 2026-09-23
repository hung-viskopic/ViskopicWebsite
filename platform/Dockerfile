FROM node:22-bookworm-slim AS node-build
WORKDIR /app
COPY package*.json ./
COPY apps/api/package.json apps/api/package.json
COPY apps/web/package.json apps/web/package.json
RUN npm ci
COPY apps/api apps/api
COPY apps/web apps/web
COPY database database
RUN npm run build

FROM python:3.12-slim-bookworm AS python-runtime
COPY services/worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM python-runtime
WORKDIR /app
ENV NODE_ENV=production
ENV WEB_ROOT=/app/web
COPY --from=node-build /usr/local /usr/local
COPY --from=node-build /app/node_modules /app/node_modules
COPY --from=node-build /app/apps/api/node_modules /app/apps/api/node_modules
COPY --from=node-build /app/apps/api/dist /app/apps/api/dist
COPY --from=node-build /app/apps/web/dist /app/web
COPY database /app/database
COPY services/worker /app/worker
COPY start-production.mjs /app/start-production.mjs
RUN useradd --uid 10001 app
USER app
EXPOSE 10000
CMD ["node", "/app/start-production.mjs"]

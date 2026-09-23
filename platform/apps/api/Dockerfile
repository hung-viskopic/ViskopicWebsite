FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
COPY apps/api/package.json apps/api/package.json
COPY apps/web/package.json apps/web/package.json
RUN npm ci
COPY apps/api apps/api
COPY database database
RUN npm run build -w apps/api
USER node
EXPOSE 10000
CMD ["node", "apps/api/dist/main.js"]

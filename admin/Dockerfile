# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Build-arg → ENV для Vite: значение впечатывается в бандл на этапе build.
# Без него admin/src/api.ts шлёт запросы на собственный домен, где SPA-fallback
# отдаёт index.html с кодом 200 — и дашборд падает на «JSON», которого нет.
ARG VITE_API_BASE=https://api-english.krichigindocs.ru
ENV VITE_API_BASE=$VITE_API_BASE

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npm run build

# ─── Stage 2: Serve ──────────────────────────────────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

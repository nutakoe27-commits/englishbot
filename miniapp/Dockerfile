# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Устанавливаем зависимости
COPY package.json ./
RUN npm install --no-audit --no-fund

# Копируем исходники и собираем
COPY . .
RUN npm run build

# ─── Stage 2: Serve ──────────────────────────────────────────────────────────
FROM nginx:alpine

# Копируем собранную статику
COPY --from=builder /app/dist /usr/share/nginx/html

# Встроенный nginx.conf с SPA-fallback (все маршруты → index.html)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Build-args → ENV для Vite. Vite впечатывает значения import.meta.env.VITE_*
# в бандл на этапе build, поэтому их нужно передавать именно здесь.
ARG VITE_BOT_USERNAME=kmo_ai_english_bot
ENV VITE_BOT_USERNAME=$VITE_BOT_USERNAME

# Базовый URL backend API. Дефолт = прод, чтобы старые билды (без явного
# build-arg) продолжали работать. Staging пересобирается с
# VITE_API_BASE=https://api-english-test.krichigindocs.ru.
ARG VITE_API_BASE=https://api-english.krichigindocs.ru
ENV VITE_API_BASE=$VITE_API_BASE

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

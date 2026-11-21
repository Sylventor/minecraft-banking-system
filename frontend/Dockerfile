FROM node:22-alpine AS build
WORKDIR /app
ARG VITE_PROJECT_NAME="Banking System"
ARG VITE_SERVER_URL=""
ARG VITE_BANK_LOGO_URL=""
ENV VITE_PROJECT_NAME=$VITE_PROJECT_NAME VITE_SERVER_URL=$VITE_SERVER_URL VITE_BANK_LOGO_URL=$VITE_BANK_LOGO_URL
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack pnpm install --frozen-lockfile
COPY frontend/ ./
RUN corepack pnpm run build
FROM nginx:1.30.7-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx/frontend.conf /etc/nginx/conf.d/default.conf
# Public assets must be readable by the unprivileged Nginx workers.
RUN chmod -R u=rwX,go=rX /usr/share/nginx/html && chmod 0644 /etc/nginx/conf.d/default.conf

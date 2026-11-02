FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /web/dist /srv
COPY Caddyfile.docker /etc/caddy/Caddyfile
EXPOSE 8080

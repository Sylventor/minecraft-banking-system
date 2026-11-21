FROM node:22-alpine
WORKDIR /app
COPY atm/package.json atm/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY atm/src ./src
# Normalize checkout permissions before dropping privileges; keep code root-owned.
RUN chmod -R u=rwX,go=rX /app && mkdir /state && chown node:node /state
USER node
ENV ATM_CONFIG_FILE=/config/config.json ATM_BOT_SECRETS_FILE=/config/bot-secrets.env ATM_STATE_DIR=/state
CMD ["node", "src/main.js"]

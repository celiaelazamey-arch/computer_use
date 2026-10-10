FROM node:22-bookworm-slim

ENV NODE_ENV=development
WORKDIR /workspace

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 8787
CMD ["npm", "run", "dev", "--", "--ip", "0.0.0.0"]

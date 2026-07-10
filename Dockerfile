FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY server.js ./server.js
COPY public ./public
EXPOSE 8080
CMD ["npm", "start"]

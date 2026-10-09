FROM node:20-alpine

ARG VITE_TTS_SERVER_URL=http://localhost:8000
ENV VITE_TTS_SERVER_URL=$VITE_TTS_SERVER_URL

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 4173

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "4173"]

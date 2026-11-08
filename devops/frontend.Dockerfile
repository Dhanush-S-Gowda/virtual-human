FROM node:22-alpine AS build
WORKDIR /app/frontend
ARG VITE_BACKEND_URL=/api
ARG VITE_CV_URL=
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL VITE_CV_URL=$VITE_CV_URL
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
COPY TalkingHead/ /app/TalkingHead/
RUN npm run build && mkdir -p dist/avatars && cp -r /app/TalkingHead/avatars/. dist/avatars/

FROM nginx:stable-alpine
COPY devops/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 4173
CMD ["nginx", "-g", "daemon off;"]

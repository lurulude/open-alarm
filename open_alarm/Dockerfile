# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json /frontend/package.json
RUN npm install --no-audit --no-fund
COPY frontend /frontend
RUN npm run build

FROM ghcr.io/home-assistant/base-python:3.13-alpine3.21
ARG BUILD_VERSION="0.1.0-dev"
ARG BUILD_ARCH="amd64"

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY --from=frontend-build /frontend/dist /app/frontend_dist
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

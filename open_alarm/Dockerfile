# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json /frontend/package.json
RUN npm install --no-audit --no-fund
COPY frontend /frontend
RUN node license-audit.mjs node_modules /frontend/npm-packages.json \
    && mkdir -p /frontend/runtime-licenses \
    && for package in react react-dom scheduler; do \
         license_file="$(find "node_modules/${package}" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'LICENCE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) | head -n 1)"; \
         test -n "${license_file}"; \
         cp "${license_file}" "/frontend/runtime-licenses/${package}-LICENSE"; \
       done \
    && npm run build

FROM ghcr.io/home-assistant/base-python:3.13-alpine3.21
ARG BUILD_VERSION="0.1.0-dev"
ARG BUILD_ARCH="amd64"

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY license_audit.py /app/license_audit.py
RUN mkdir -p /app/licenses/python \
    && pip3 install --no-cache-dir -r /app/requirements.txt \
    && python3 /app/license_audit.py \
       --report /app/licenses/python-packages.json \
       --copy-license-files /app/licenses/python \
       fastapi uvicorn pydantic websockets \
    && rm /app/license_audit.py

COPY backend /app/backend
COPY --from=frontend-build /frontend/dist /app/frontend_dist
COPY --from=frontend-build /frontend/npm-packages.json /app/licenses/npm-packages.json
COPY --from=frontend-build /frontend/runtime-licenses /app/licenses/frontend
COPY LICENSE /app/licenses/Open-Alarm-LICENSE
COPY THIRD_PARTY_NOTICES.md /app/licenses/THIRD_PARTY_NOTICES.md
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

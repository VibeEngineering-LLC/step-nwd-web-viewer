# Сборка из корня репозитория:  docker build -f hosting/Dockerfile -t viewer-service:local .
# WITH_NWD=0 собирает образ без парсера NWD (nwd-reader клонируется при сборке, в репозиторий не входит).
ARG WITH_NWD=1

FROM python:3.12-slim-bookworm AS nwd-build
ARG WITH_NWD
ARG NWD_READER_COMMIT=b4808730e32132cf656e7d622bf8232b30918ff9
RUN mkdir -p /out
COPY scripts/nwd /src/nwd
RUN if [ "$WITH_NWD" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
      && rm -rf /var/lib/apt/lists/* \
      && git clone https://github.com/1817716374/nwd-reader /src/nwd-reader \
      && git -C /src/nwd-reader checkout "$NWD_READER_COMMIT" \
      && cmake -S /src/nwd -B /src/build -DCMAKE_BUILD_TYPE=Release -DNWD_READER_DIR=/src/nwd-reader \
      && cmake --build /src/build --target nwd_dump -j2 \
      && cp /src/build/nwd_dump /out/nwd_dump; \
    fi

# Кодер meshopt: исходники zeux/meshoptimizer v1.3 (коммит закреплён; исходники проверены перед сборкой)
FROM python:3.12-slim-bookworm AS meshopt-build
ARG MESHOPT_COMMIT=9e1f07b159d3cb777f1c67ed31fc11fd117986f4
RUN apt-get update && apt-get install -y --no-install-recommends g++ git ca-certificates && rm -rf /var/lib/apt/lists/* \
    && git clone https://github.com/zeux/meshoptimizer /src/meshopt \
    && git -C /src/meshopt checkout "$MESHOPT_COMMIT" \
    && mkdir -p /out && g++ -O2 -shared -fPIC -o /out/libmeshopt.so /src/meshopt/src/*.cpp

FROM python:3.12-slim-bookworm AS vendor
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
ARG THREE=0.160.0
RUN set -e; B=https://cdn.jsdelivr.net/npm/three@$THREE; D=/vendor/three; \
    mkdir -p $D/build $D/examples/jsm/controls $D/examples/jsm/loaders $D/examples/jsm/environments $D/examples/jsm/utils $D/examples/jsm/libs; \
    curl -fsSL $B/build/three.module.js -o $D/build/three.module.js; \
    for f in controls/OrbitControls.js loaders/GLTFLoader.js environments/RoomEnvironment.js utils/BufferGeometryUtils.js libs/meshopt_decoder.module.js; do \
      curl -fsSL $B/examples/jsm/$f -o $D/examples/jsm/$f; done
COPY hosting/vendor.sha256 /vendor.sha256
RUN cd /vendor && sha256sum -c /vendor.sha256

FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libx11-6 libexpat1 && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin app
COPY hosting/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY --from=nwd-build /out/ /usr/local/bin/
COPY --from=meshopt-build /out/libmeshopt.so /usr/local/lib/libmeshopt.so
COPY --from=vendor /vendor /app/vendor
COPY scripts /app/scripts
COPY assets /app/assets
COPY hosting/app /app/hosting_app
RUN mkdir -p /data && chown 10001:10001 /data \
    && printf '#!/bin/sh\nexec python -m hosting_app.cli "$@"\n' > /usr/local/bin/viewerctl \
    && chmod 0755 /usr/local/bin/viewerctl
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001

# Тесты:  docker build -f hosting/Dockerfile --target test -t viewer-test:pinned .  &&  docker run --rm viewer-test:pinned
FROM base AS test
USER root
COPY hosting/requirements.txt hosting/requirements-test.txt /tmp/
RUN cat /tmp/requirements.txt /tmp/requirements-test.txt > /tmp/req-all.txt \
    && pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/req-all.txt \
    && rm /tmp/req-all.txt /tmp/requirements-test.txt
COPY hosting/tests /app/tests
USER 10001:10001
CMD ["python", "-m", "pytest", "-x", "-q", "/app/tests"]

FROM base AS runtime
CMD ["uvicorn", "hosting_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]

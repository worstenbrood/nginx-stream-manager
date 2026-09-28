FROM alpine:3.23
RUN apk add --no-cache nginx nginx-mod-stream python3 nftables iproute2 nodejs openssl
ARG UCI_REV=74f6277aabffc943d026f406df57c22595134c42
RUN apk add --no-cache --virtual .build build-base cmake git libubox-dev \
    && git init /tmp/uci \
    && git -C /tmp/uci remote add origin https://github.com/openwrt/uci.git \
    && git -C /tmp/uci fetch --depth 1 origin "$UCI_REV" \
    && git -C /tmp/uci checkout --detach FETCH_HEAD \
    && git -C /tmp/uci rev-parse HEAD > /uci-source-revision \
    && cmake -S /tmp/uci -B /tmp/uci/build -DBUILD_LUA=OFF -DCMAKE_INSTALL_PREFIX=/usr \
    && cmake --build /tmp/uci/build \
    && cmake --install /tmp/uci/build \
    && apk add --no-cache libubox \
    && apk del .build \
    && rm -rf /tmp/uci
WORKDIR /src
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "tests/integration_nginx.py"]

FROM alpine:3.20

ARG TARGETARCH

RUN apk add --no-cache \
    python3 \
    py3-cryptography \
    ca-certificates \
    curl \
    unzip

ARG XRAY_VERSION=v24.11.30
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) XRAY_ARCH="64" ;; \
        arm64) XRAY_ARCH="arm64-v8a" ;; \
        *) XRAY_ARCH="64" ;; \
    esac; \
    curl -sSL "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip" -o /tmp/xray.zip; \
    unzip /tmp/xray.zip -d /usr/local/bin/ xray geosite.dat geoip.dat; \
    chmod +x /usr/local/bin/xray; \
    mkdir -p /usr/local/share/xray /etc/xray; \
    mv /usr/local/bin/geosite.dat /usr/local/share/xray/; \
    mv /usr/local/bin/geoip.dat /usr/local/share/xray/; \
    rm -rf /tmp/xray.zip

COPY entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod +x /usr/local/bin/entrypoint.py

ENV XRAY_LOCATION_ASSET=/usr/local/share/xray

ENTRYPOINT ["/usr/local/bin/entrypoint.py"]

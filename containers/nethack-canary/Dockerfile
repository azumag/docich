FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

ARG NETHACK_VERSION=5.0.0
ARG NETHACK_ARCHIVE=nethack-500-src.tgz
ARG NETHACK_SHA256=2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl git groff gzip liblua5.4-dev libncurses-dev \
       lua5.4 pkg-config uuid-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl --fail --location --silent --show-error \
      "https://nethack.org/download/${NETHACK_VERSION}/${NETHACK_ARCHIVE}" \
      --output "/tmp/${NETHACK_ARCHIVE}" \
    && echo "${NETHACK_SHA256}  /tmp/${NETHACK_ARCHIVE}" | sha256sum -c - \
    && mkdir -p /build/nethack \
    && tar -xzf "/tmp/${NETHACK_ARCHIVE}" -C /build/nethack --strip-components=1 \
    && rm "/tmp/${NETHACK_ARCHIVE}"

WORKDIR /build/nethack
# NetHack 5.0 Unix paths are compile-time settings. Keep immutable support
# data under /opt and route every mutable game file into the one bind-mounted
# episode playground. Wizard/explore/shell access stays disabled.
# WANT_SYSTEM_LUA must be present while setup.sh expands linux.500; passing it
# only to the later make invocation is too late and causes bundled-Lua fetches.
RUN cat >> sys/unix/hints/linux.500 <<'EOF'

# docich P5h isolated canary overrides
WANT_SYSTEM_LUA=1
PREFIX=/opt/nethack
HACKDIR=/opt/nethack/playground
INSTDIR=$(HACKDIR)
VARDIR=$(HACKDIR)
CHOWN=true
CHGRP=true
CFLAGS+=-DVAR_PLAYGROUND='"/canary/episode/playground"'
EOF
RUN sed -i 's/^WIZARDS=.*/WIZARDS=/' sys/unix/sysconf \
    && sed -i 's/^EXPLORERS=.*/EXPLORERS=/' sys/unix/sysconf \
    && sed -i 's/^MAXPLAYERS=.*/MAXPLAYERS=1/' sys/unix/sysconf \
    && printf '\nSHELLERS=\nDUMPLOGFILE=/canary/episode/playground/dumps/%%n.%%d.log\n' >> sys/unix/sysconf \
    && cd sys/unix \
    && sh setup.sh hints/linux.500 \
    && cd /build/nethack \
    && make install WANT_WIN_TTY=1

RUN test -x /opt/nethack/playground/nethack \
    && /opt/nethack/playground/nethack --version >/dev/null

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime
LABEL org.docich.nethack-canary.abi="1" \
      org.docich.nethack.version="5.0.0" \
      org.docich.nethack.source-sha256="2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gzip liblua5.4-0 libncurses6 libtinfo6 libuuid1 tmux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/nethack /opt/nethack
RUN chmod -R a+rX /opt/nethack \
    && ln -s /opt/nethack/playground/nethack /usr/local/bin/nethack

WORKDIR /opt/docich
COPY src /opt/docich/src
COPY brains /opt/docich/brains

ENV PYTHONPATH=/opt/docich/src \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp/home \
    TERM=xterm-256color

ENTRYPOINT ["python3", "-m", "docich.nethack_canary_worker"]

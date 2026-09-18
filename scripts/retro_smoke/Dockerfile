# Throwaway Linux image with the real retro CLI games, for run.sh.
# Ubuntu 24.04 matches the deploy target's package set (games live in /usr/games).
# Never deployed: this image only exists to observe real panes offline.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 TERM=xterm-256color
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tmux python3 procps ca-certificates \
      bastet moon-buggy pacman4console ninvaders nsnake \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work

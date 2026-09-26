FROM debian:bookworm-slim

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
# Chromium は glibc 前提のため、他のアプリ(Alpine)とは別に Debian を使う。
# ヘッドレスは Cloudflare Turnstile に弾かれるため、Xvfb を表示先にしてヘッドありで動かす。
# 画面は CDP の screencast で操作画面へ送るため、VNC は使わない。
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium xvfb fonts-noto-cjk python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt
COPY src/ .
EXPOSE 8080
CMD ["python3", "-u", "main.py"]

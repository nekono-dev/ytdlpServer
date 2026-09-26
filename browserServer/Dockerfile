FROM debian:bookworm-slim

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
# Chromium は glibc 前提のため、他のアプリ(Alpine)とは別に Debian を使う。
# xvfb + x11vnc + novnc(websockify) で、ユーザのブラウザから操作できる画面を提供する。
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium xvfb x11vnc novnc websockify fonts-noto-cjk \
        python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt
COPY src/ .
EXPOSE 8080 6080
CMD ["python3", "-u", "main.py"]

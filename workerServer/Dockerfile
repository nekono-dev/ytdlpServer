FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
# nodejs: yt-dlp が YouTube の JS チャレンジ(EJS)を解くために必要。
# deno は、公式インストーラが Alpine(musl)で動作せず(curl も無い)、
# apk 版(2.0.6)は yt-dlp から unsupported 扱いとなるため、node を使用する。
RUN apk --no-cache --update add python3 py3-pip gcc ffmpeg mutagen~=1.47.0 nodejs

COPY requirements.txt .
COPY yt-dlp.conf /etc/yt-dlp.conf

RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt
COPY src/ .
COPY entrypoint.sh .
RUN chmod +x /workspace/entrypoint.sh

WORKDIR /tmpdownload
ENTRYPOINT ["/workspace/entrypoint.sh"]
FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
RUN apk --no-cache --update add python3 py3-pip gcc ffmpeg mutagen~=1.47.0

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt
COPY src/ .
COPY entrypoint.sh .
WORKDIR /download

ENTRYPOINT [ "/bin/sh", "/workspace/entrypoint.sh" ]
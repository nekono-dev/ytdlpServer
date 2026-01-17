FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
RUN apk --no-cache --update add python3 py3-pip

COPY requirements.txt .
## --break-system-packages: alphineではapkを使ってpipパッケージを導入する変更が行われたことに対する対処
RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt
COPY src/ .
COPY entrypoint.sh .
EXPOSE 5000
ENTRYPOINT [ "/bin/sh", "./entrypoint.sh" ]
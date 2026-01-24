FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
RUN apk --no-cache --update add python3 py3-pip nginx

COPY requirements.txt .

RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt
COPY src/ .
COPY entrypoint.sh .
RUN chmod +x /workspace/entrypoint.sh
EXPOSE 5000
ENTRYPOINT ["/workspace/entrypoint.sh"]
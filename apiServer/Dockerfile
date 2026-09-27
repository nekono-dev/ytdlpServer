# ビルドコンテキストはリポジトリ直下 (updater.py を workerServer と共有するため)。
# docker-compose.yml の api サービスの `build.context: .` を参照。
FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
# nodejs: yt-dlp が YouTube の JS チャレンジ(EJS)を解くために必要。
RUN apk --no-cache --update add python3 py3-pip curl nginx nodejs

COPY apiServer/requirements.txt .
COPY apiServer/yt-dlp.conf /etc/yt-dlp.conf

RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt

# bgutil の yt-dlp プラグインを、標準の置き場へ配置する (workerServer と同じ内容)。
RUN mkdir -p /etc/yt-dlp/plugins/bgutil && \
    SITE=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") && \
    cp -r "$SITE/yt_dlp_plugins" /etc/yt-dlp/plugins/bgutil/

COPY apiServer/src/ .
COPY apiServer/entrypoint.sh .
RUN chmod +x /workspace/entrypoint.sh

# yt-dlp の初回導入 (イメージ同梱のフォールバック版)。api 自身は yt-dlp を更新しない
# (dispatcher が更新する)。導入先 (ytdlp-bin ボリューム) が空の場合のフォールバック。
# updater.py は workerServer と同一内容 (ビルドコンテキストから直接コピーする)。
COPY workerServer/src/updater.py .
ENV YTDLP_DIR=/opt/ytdlp-image
RUN python3 /workspace/updater.py
ENV YTDLP_DIR=/opt/ytdlp
RUN mkdir -p /opt/ytdlp
ENV PATH="/opt/ytdlp/current:/opt/ytdlp-image/current:${PATH}"

EXPOSE 5000
ENTRYPOINT ["/workspace/entrypoint.sh"]

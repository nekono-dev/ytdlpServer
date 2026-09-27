# ビルドコンテキストはリポジトリ直下 (updater.py を apiServer と共有するため)。
# docker-compose.yml の worker/dispatcher サービスの `build.context: .` を参照。
FROM alpine:3.21

WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive
# nodejs: yt-dlp が YouTube の JS チャレンジ(EJS)を解くために必要。
# deno は、公式インストーラが Alpine(musl)で動作せず(curl も無い)、
# apk 版(2.0.6)は yt-dlp から unsupported 扱いとなるため、node を使用する。
RUN apk --no-cache --update add python3 py3-pip curl gcc ffmpeg mutagen~=1.47.0 nodejs

COPY workerServer/requirements.txt .
COPY workerServer/yt-dlp.conf /etc/yt-dlp.conf

RUN pip3 install --break-system-packages --no-cache --upgrade -r requirements.txt

# bgutil の yt-dlp プラグイン (getpot_bgutil*.py) を、バイナリ版 yt-dlp が読む
# 標準の置き場 (/etc/yt-dlp/plugins) へ配置する。プラグインは yt-dlp の更新の対象外
# (このイメージのビルド時に固定される)。
RUN mkdir -p /etc/yt-dlp/plugins/bgutil && \
    SITE=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") && \
    cp -r "$SITE/yt_dlp_plugins" /etc/yt-dlp/plugins/bgutil/

COPY workerServer/src/ .
COPY workerServer/entrypoint.sh .
RUN chmod +x /workspace/entrypoint.sh

# yt-dlp の初回導入 (イメージ同梱のフォールバック版)。
# 導入先 (ytdlp-bin ボリューム、既定 /opt/ytdlp) が空・壊れている場合に使われる。
# 取得に失敗した場合はビルドを失敗させる (specs/workerServer/design.md)。
ENV YTDLP_DIR=/opt/ytdlp-image
RUN python3 /workspace/updater.py
ENV YTDLP_DIR=/opt/ytdlp
RUN mkdir -p /opt/ytdlp
ENV PATH="/opt/ytdlp/current:/opt/ytdlp-image/current:${PATH}"

ENTRYPOINT ["/workspace/entrypoint.sh"]

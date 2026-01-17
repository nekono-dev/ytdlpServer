# ytdlp Server

ytdlp Sever is a API Endpoint for launch yt-dlp on your network.

## tl;dr

Ubuntuサーバを用意して、以下を実行

```sh
sudo apt update
sudo apt install docker.io cifs-utils
sudo gpasswd --add $USER docker
newgrp docker
sudo wget https://github.com/docker/compose/releases/download/v2.4.1/docker-compose-linux-x86_64 -P /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

docker-compose.yamlの`volumes`のディレクトリをカスタマイズする。
以下はsamba共有ディレクトリを`/mnt/video`に設定する例

```sh
sudo mkdir -p /mnt/video
## if samba
sudo tee -a "//<your windows ipaddr>/<your sharing path>   /mnt/video   cifs  nofail,_netdev,x-systemd.automount,user=<your username>,password=<your password>,file_mode=0666,dir_mode=0777  0  0" /etc/fstab
sudo mount -a
```

サーバを起動する。

```sh
docker-compose up -d --scale worker=4
```

起動後、`http://<Your Server IPaddr>:5000/ytdlp`に対してPOSTリクエストを送ると、設定したディレクトリに動画がダウンロードできる。

## サーバの使い方

APIサーバに、以下のような POSTリクエストを送信する。

   ```sh
   curl -H "Content-Type: application/json" -X POST "http://localhost:5000/ytdlp" -d "{\"url\": "https://www.youtube.com/watch?v=XXXXXXXXXX", \"options\": \"--format bv*+ba/best\", \"savedir\": \"unsorted\"}
   ```

iOSショートカットなどを作成すると楽に操作できる。

<details><summary>image</summary>

![iOS Shortcut example](.github/images/image.png)

</details>

## API option

| option  | type   | description                                            |
| ------- | ------ | ------------------------------------------------------ |
| url     | string | yt-dlp でダウンロードする動画URL                       |
| options | string | yt-dlp コマンドラインに利用するオプション              |
| savedir | string | 指定した場合、サブディレクトリを作成して動画を保存する |

## オプションのヒント

すべてのオプションはyt-dlpのオプションに準じる。よくある設定は以下。

- Youtubeの動画音声がローカライズされない:
  - `extractor-args` に `youtube:lang=ja` などを設定する。
  - 備考: https://github.com/yt-dlp/yt-dlp/issues/387#issuecomment-1195182084

- ファイル名が文字化けする
  - `--windows-filenames`オプションを付与する

- ログインが必要
  - `-u <ユーザ名> -p <パスワード>`オプションを付与する。

- フォーマットが意図通りにならない（webbmなど）
  - `--merge-output-format mp4`などで固定する

- 再ダウンロード（同ディレクトリへ同じファイルをダウンロード）できない
  - `--force-overwrites`を設定する

## Setup

### Install Container engine

#### Install and setup docker

docker.io may charge a fee in the future.

```sh
sudo apt update
sudo apt install docker.io
sudo gpasswd --add $USER docker
newgrp docker
## if docker soket is down, reboot
sudo reboot
```

### Install docker compose

Launch in ubuntu 22.04 LTS.

https://matsuand.github.io/docs.docker.jp.onthefly/compose/install/

```sh
sudo curl -SL https://github.com/docker/compose/releases/download/v2.4.1/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

```log
ubuntu@devsv:~/git/ytdlpServer
>> docker-compose --version
Docker Compose version v2.4.1
```

### (Optional) Install cifs for mount Windows Directory

Install for mount windows samba share directory if you need.

```sh
sudo apt install cifs-utils
```

Create mout directory

```sh
sudo mkdir -p /mnt/video
```

if you need not hide your credential, you can setup `fstab` with hardcode credential.

ex. mount `¥¥192.168.3.120¥Videos`, user name is `samba`, password is `samba`. add that to `/etc/fstab`

```conf
//192.168.3.120/Videos   /mnt/video   cifs  nofail,_netdev,x-systemd.automount,user=samba,password=samba,file_mode=0666,dir_mode=0777  0  0
```

If not, create samba credential directory and credential file for connect windows share directory.

```sh
sudo mkdir -p /etc/smb-credentials/
cat << EOF | sudo tee /etc/smb-credentials/.pw
username=user
password=passwd
EOF
```

...And prevent access all user except root.

```sh
sudo chmod +600 /etc/smb-credentials/.pw
```

Edit `/etc/fstab` for mount on startup.  
ex. mount `¥¥192.168.3.120¥Videos` add...

```conf
//192.168.3.120/Videos   /mnt/video   cifs  nofail,_netdev,x-systemd.automount,credentials=/etc/smb-credentials/.pw,file_mode=0666,dir_mode=0777  0  0
```

Try mount directory.

```sh
sudo mount -a
```

## Build / Install

### Install as container

Build container image.

```sh
docker-compose build
```

Attention: Very long to build, wait a moment.

## Launch

### Launch as container

Edit `docker-compose.yml` set your directory to `volumes` for download.

```yml
worker:
  build: ./workerServer
  depends_on:
    - redis
  environment:
    REDIS_URL: redis://redis
  volumes:
    - /mnt/video:/download
  working_dir: /download
  restart: always
```

Lauch container with mounting download path.

```sh
## set scale of workers.
docker-compose up -d --scale worker=4
## show log
docker-compose logs -f
```
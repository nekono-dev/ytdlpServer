
# Redis

```sh
docker run --name redis-ytdlp -p 6379:6379 -d --rm redis:8.4.0
```

# Redis Insight(DB閲覧)

```sh
docker run -d --name redisinsight -p 5540:5540 redis/redisinsight:latest
```

# API Server

```sh
cd apiServer
# build
docker build . -t ytdlpserver-api
# Run debug mode with redis
docker run --rm --name ytdlp-api -p 5000:5000 -e DEBUG=true -e REDIS_URL=redis://$(hostname -I | awk '{print $1}'):6379 ytdlpserver-api:latest
```

```log
Requirement already satisfied: yt-dlp in /usr/lib/python3.12/site-packages (2025.12.8)
INFO: yt-dlp updated to latest version.
INFO: Connected to Redis at redis://192.168.3.151:6379
INFO: Start ytdlpServer port: 5000
```

# Worker Server

```sh
cd workerServer
docker build . -t ytdlpserver-worker

# Run debug mode with redis
docker run --rm --name ytdlp-worker -v /mnt/video:/download -e REDIS_URL=redis://$(hostname -I | awk '{print $1}'):6379 ytdlpserver-worker:latest
```
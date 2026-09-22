# PUBG Map Rotation — 1.0 build 40

Unraid-friendly Docker WebUI that extracts PUBG map-service reports and publishes current and next rotations to this repository.

## Features

- Load a PUBG report URL or paste report tables manually.
- Detect available weeks and start dates automatically.
- Publish `maparray`, `maparray_sea`, `nextweek`, and `nextweek_sea`.
- Combine EU and NA in normal map files; keep SEA separate.
- Fill unavailable rotations with `Not used this season`.
- Support multiple Discord webhook profiles.
- Persist settings under `/config`.
- Run weekly updates using a configured UTC day and time.

## Unraid installation

### Downloaded image

After the GitHub Actions package is published, install the Unraid template from `unraid/pubgmaprotation.xml`, or run:

```bash
docker pull ghcr.io/catjamstudio/pubgmaprotation:docker-app
docker run -d --name=pubgmaprotation --restart=unless-stopped -p 18082:8080 -v /mnt/user/appdata/pubgmaprotation/config:/config ghcr.io/catjamstudio/pubgmaprotation:docker-app
```

```bash
cd /mnt/user/appdata
git clone -b docker-app https://github.com/catjamstudio/pubgmaprotation.git pubgmaprotation-src
mkdir -p /mnt/user/appdata/pubgmaprotation/config
cd /mnt/user/appdata/pubgmaprotation-src
docker build -t pubgmaprotation:dev .
docker run -d --name=pubgmaprotation --restart=unless-stopped -p 18082:8080 -v /mnt/user/appdata/pubgmaprotation/config:/config pubgmaprotation:dev
```

Open `http://UNRAID-IP:18082`.

To update an existing installation:

```bash
cd /mnt/user/appdata/pubgmaprotation-src
git pull origin docker-app
docker build --no-cache -t pubgmaprotation:dev .
docker stop pubgmaprotation 2>/dev/null || true
docker rm pubgmaprotation 2>/dev/null || true
docker run -d --name=pubgmaprotation --restart=unless-stopped -p 18082:8080 -v /mnt/user/appdata/pubgmaprotation/config:/config pubgmaprotation:dev
```

## Settings

The GitHub token is edited as a masked field in the WebUI and persisted under `/config/settings.yaml`. Weekly scheduling uses UTC:

```yaml
rollover_timestamp: 1788915600
schedule_weekday: 2
schedule_time: "01:00"
automatic_updates: true
```

`schedule_weekday` uses Monday `0` through Sunday `6`.

Multiple Discord webhooks use this format:

```yaml
discord_webhooks:
  - name: "GitHub Push"
    username: "PUBG Map Rotation"
    url: "https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN"
    avatar_url: "https://example.com/avatar.png"
```

## Runtime

The scheduler checks every 30 seconds. At the configured UTC time it fetches the report, determines current and next week, updates the four GitHub files on `docker-app`, and sends Discord notifications.

```bash
docker logs -f pubgmaprotation
```

Runtime state belongs under `/config`. Do not commit GitHub tokens, webhook URLs, or production settings.

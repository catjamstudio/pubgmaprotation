# PUBG Map Rotation

Unraid-friendly web app that extracts PUBG map-service reports and publishes current and next rotations to this repository.

Secrets belong in `/config/settings.yaml` or the `GITHUB_TOKEN` and `DISCORD_WEBHOOKS` environment variables. The WebUI never displays them. `DISCORD_WEBHOOKS` accepts a comma-separated list. The weekly update day and UTC time are editable in the WebUI and persist in `/config/settings.yaml`.

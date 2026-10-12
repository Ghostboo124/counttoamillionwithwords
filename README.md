# counttoamillionwithwords

## This project's function is merged with https://github.com/0x62b/countingbot

This is a Slack bot that moderates a channel, where you count to a million with words (eg. "six thousand seven hundred and forty one")

reacts with :white-tick: when number correct\
reacts with :sixseven: when number ends with 67\
reacts with :ok_hand: when number ends with 69\
reacts with :chess-brilliant: when number is prime\
reacts with :tada: when number modulo 100 is 0

There are two routes for commands:
* one to override the number
* one to view the leaderboard

## Required environment variables

* `SLACK_BOT_TOKEN`
* `SLACK_SIGNING_SECRET`
* `COUNTING_CHANNEL`

## Catch-up after downtime

The bot now persists `current`, `last_user`, `scores`, and `last_processed_ts` in `state.json` (plus legacy `number.txt`, `user.txt`, and `scores.json` files for compatibility).

On startup (before first live message is processed), it can replay missed channel messages using Slack history and process them oldest-first.

### Catch-up configuration

* `SLACK_CATCHUP_ENABLED` (default: `true`) — enable/disable history catch-up.
* `SLACK_CATCHUP_MAX_MESSAGES` (default: `200`) — max number of history messages replayed per startup.
* `SLACK_CATCHUP_INITIAL_TIMESTAMP` (optional) — first-deploy bootstrap timestamp.
* `STATE_DIR` (default: current directory) — where state files are stored.

### First deployment safety

If no checkpoint exists yet:
* with no `SLACK_CATCHUP_INITIAL_TIMESTAMP`, the bot **does not replay** full channel history.
* with `SLACK_CATCHUP_INITIAL_TIMESTAMP`, it replays only messages newer than that timestamp.

### Slack permissions needed

Catch-up requires history read scope for the counting channel (`conversations.history` compatible scope for your channel type) in addition to the bot’s existing message/reaction permissions.

## Docker / persistent state

Use Docker Compose to keep state durable across restarts:

```bash
docker compose up --build
```

`docker-compose.yml` mounts `./data` to `/app/data` and sets `STATE_DIR=/app/data`, so checkpoints and counters survive container restarts.

import json
import os
import threading
from decimal import Decimal, InvalidOperation
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from parser import parse

load_dotenv()

def _env_true(name, default):
  return os.getenv(name, default).lower() in ("1", "true", "yes", "on")

slack_app = App(
  token=os.getenv("SLACK_BOT_TOKEN"),
  signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
  token_verification_enabled=_env_true("SLACK_TOKEN_VERIFICATION_ENABLED", "true")
)

CHANNEL = os.getenv("COUNTING_CHANNEL")
APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
STATE_DIR = os.getenv("STATE_DIR", ".")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NUMBER_FILE = os.path.join(STATE_DIR, "number.txt")
USER_FILE = os.path.join(STATE_DIR, "user.txt")
SCORES_FILE = os.path.join(STATE_DIR, "scores.json")
CATCHUP_ENABLED = _env_true("SLACK_CATCHUP_ENABLED", "true")
CATCHUP_MAX_MESSAGES = int(os.getenv("SLACK_CATCHUP_MAX_MESSAGES", "200"))
CATCHUP_INITIAL_TIMESTAMP = os.getenv("SLACK_CATCHUP_INITIAL_TIMESTAMP")

_catchup_done = False
_catchup_lock = threading.Lock()

def prime(n):
  ret = True
  if n <= 1:
    ret = False
  else:
    for i in range(2, int(n ** 0.5) + 1):
      if n % i == 0:
        ret = False
        break
  return ret

def _ensure_state_dir():
  os.makedirs(STATE_DIR, exist_ok=True)

def _atomic_write(path, content):
  _ensure_state_dir()
  with NamedTemporaryFile("w", dir=STATE_DIR, delete=False) as tmp:
    tmp.write(content)
    tmp.flush()
    os.fsync(tmp.fileno())
    temp_name = tmp.name
  os.replace(temp_name, path)

def _parse_int(value, default=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default

def _read_legacy_state():
  current = 0
  if os.path.exists(NUMBER_FILE):
    with open(NUMBER_FILE, "r") as f:
      current = _parse_int(f.read().strip() or "0", 0)

  last_user = ""
  if os.path.exists(USER_FILE):
    with open(USER_FILE, "r") as f:
      last_user = f.read()

  scores = {}
  if os.path.exists(SCORES_FILE):
    with open(SCORES_FILE, "r") as f:
      try:
        scores = json.load(f)
      except json.JSONDecodeError:
        scores = {}

  return {
    "current": current,
    "last_user": last_user,
    "scores": scores,
    "last_processed_ts": None
  }

def load_state():
  if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
      try:
        loaded = json.load(f)
      except json.JSONDecodeError:
        loaded = {}
    return {
      "current": _parse_int(loaded.get("current"), 0),
      "last_user": loaded.get("last_user") or "",
      "scores": loaded.get("scores") or {},
      "last_processed_ts": loaded.get("last_processed_ts")
    }
  return _read_legacy_state()

def save_state(state):
  normalized = {
    "current": _parse_int(state.get("current"), 0),
    "last_user": state.get("last_user") or "",
    "scores": state.get("scores") or {},
    "last_processed_ts": state.get("last_processed_ts")
  }
  _atomic_write(STATE_FILE, json.dumps(normalized))
  _atomic_write(NUMBER_FILE, str(normalized["current"]))
  _atomic_write(USER_FILE, normalized["last_user"])
  _atomic_write(SCORES_FILE, json.dumps(normalized["scores"]))

def _ts_to_decimal(ts):
  if ts is None:
    return None
  try:
    return Decimal(str(ts))
  except (InvalidOperation, TypeError):
    return None

def _is_newer_ts(ts, reference):
  ts_d = _ts_to_decimal(ts)
  ref_d = _ts_to_decimal(reference)
  if ts_d is None:
    return False
  if ref_d is None:
    return True
  return ts_d > ref_d

def _is_message_event_eligible(event):
  if event.get("channel") != CHANNEL:
    return False
  if event.get("subtype"):
    return False
  if not event.get("user"):
    return False
  if event.get("bot_id"):
    return False
  if not event.get("text"):
    return False
  return True

def process_message_event(event, client):
  if not _is_message_event_eligible(event):
    return

  state = load_state()
  event_ts = event.get("ts")
  if not _is_newer_ts(event_ts, state.get("last_processed_ts")):
    return

  text = event.get("text")
  text = text.replace("-", "")
  parsed = parse(text)
  current = state["current"]
  last_user = state["last_user"]
  scores = state["scores"]

  user_id = event.get("user")
  reactions = []

  if parsed != -1:
    if parsed == current + 1 and user_id != last_user:
      reactions.append("white_check_mark")

      if str(parsed).endswith("69"):
        reactions.append("ok_hand")
      
      if str(parsed).endswith("67"):
        reactions.append("sixseven")
      
      if prime(parsed):
        reactions.append("chess-brilliant")
      
      if parsed % 100 == 0:
        reactions.append("tada")
      
      for reaction in reactions:
        client.reactions_add(
          channel=event.get("channel"),
          name=reaction,
          timestamp=event_ts
        )

      state["current"] = parsed
      state["last_user"] = user_id
      scores[user_id] = scores.get(user_id, 0) + 1
      state["scores"] = scores
    else:
      client.reactions_add(
        channel=event.get("channel"),
        name="bangbang",
        timestamp=event_ts
      )

      if parsed != current + 1:
        client.chat_postEphemeral(
          channel=event.get("channel"),
          user=user_id,
          text=f"No, the next number {current + 1} (but in words obv)"
        )
  else:
    client.reactions_add(
      channel=event.get("channel"),
      name="thonking",
      timestamp=event_ts
    )

  state["last_processed_ts"] = event_ts
  save_state(state)

def run_catchup_if_needed(client):
  global _catchup_done
  with _catchup_lock:
    if _catchup_done:
      return

    if not CATCHUP_ENABLED:
      _catchup_done = True
      return

    state = load_state()
    oldest = state.get("last_processed_ts")
    if not oldest and CATCHUP_INITIAL_TIMESTAMP:
      oldest = CATCHUP_INITIAL_TIMESTAMP
    if not oldest:
      _catchup_done = True
      return

    replayed = 0
    cursor = None
    fetched = []
    while replayed < CATCHUP_MAX_MESSAGES:
      limit = min(200, CATCHUP_MAX_MESSAGES - replayed)
      response = client.conversations_history(
        channel=CHANNEL,
        oldest=oldest,
        inclusive=False,
        limit=limit,
        cursor=cursor
      )
      messages = response.get("messages", [])
      if not messages:
        break
      fetched.extend(messages)
      replayed += len(messages)
      cursor = response.get("response_metadata", {}).get("next_cursor")
      if not response.get("has_more") or not cursor:
        break

    fetched.sort(key=lambda m: _ts_to_decimal(m.get("ts")) or Decimal("-1"))
    for message in fetched:
      process_message_event({
        "channel": CHANNEL,
        "text": message.get("text", ""),
        "user": message.get("user"),
        "bot_id": message.get("bot_id"),
        "subtype": message.get("subtype"),
        "ts": message.get("ts")
      }, client)

    _catchup_done = True

@slack_app.event("message")
def new_message(event, say, client):
  run_catchup_if_needed(client)
  process_message_event(event, client)

def handle_override_command(data):
  text = data.get("text", "")
  split = text.split()

  if data.get("user_id") != "U092839T3A7":
    return {
      "response_type": "ephemeral",
      "text": "you don't have permissions to do this"
    }
  
  if len(split) != 1 or not split[0].isdigit():
    return {
      "response_type": "ephemeral",
      "text": "need one argument - number to set"
    }

  state = load_state()
  state["current"] = int(split[0])
  save_state(state)

  slack_app.client.chat_postMessage(
    channel=data.get("channel_id"),
    text=f"counting number updated to {split[0]}"
  )

  return {
    "response_type": "ephemeral",
    "text": f"success"
  }

def handle_leaderboard_command():
  scores = load_state()["scores"]
  
  best = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
  
  text = ""
  for i, (user, score) in enumerate(best):
    text += f"{i + 1}. <@{user}>: {score}\n"

  return {
    "response_type": "ephemeral",
    "text": text
  }

@slack_app.command("/override")
def override(ack, command, respond):
  ack()
  respond(handle_override_command(command))

@slack_app.command("/leaderboard")
def leaderboard(ack, respond):
  ack()
  respond(handle_leaderboard_command())

if __name__ == "__main__":
  SocketModeHandler(slack_app, APP_TOKEN).start()
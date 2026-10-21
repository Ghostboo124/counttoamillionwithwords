import importlib
import os
import tempfile
import unittest


class FakeClient:
  def __init__(self, history_pages=None):
    self.history_pages = history_pages or []
    self.history_calls = 0
    self.reactions = []
    self.ephemeral = []

  def reactions_add(self, channel, name, timestamp):
    self.reactions.append({
      "channel": channel,
      "name": name,
      "timestamp": timestamp
    })

  def chat_postEphemeral(self, channel, user, text):
    self.ephemeral.append({
      "channel": channel,
      "user": user,
      "text": text
    })

  def conversations_history(self, channel, oldest, inclusive, limit, cursor=None):
    self.history_calls += 1
    if not self.history_pages:
      return {"messages": [], "has_more": False}
    return self.history_pages.pop(0)


class CatchupTests(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
    os.environ["SLACK_SIGNING_SECRET"] = "test"
    os.environ["COUNTING_CHANNEL"] = "C123"
    os.environ["SLACK_TOKEN_VERIFICATION_ENABLED"] = "false"
    os.environ["STATE_DIR"] = self.temp_dir.name
    os.environ["SLACK_CATCHUP_ENABLED"] = "true"
    os.environ["SLACK_CATCHUP_MAX_MESSAGES"] = "10"
    os.environ.pop("SLACK_CATCHUP_INITIAL_TIMESTAMP", None)

    import main
    self.main = importlib.reload(main)
    self.posted_messages = []
    self.main.slack_app.client.chat_postMessage = lambda channel, text: self.posted_messages.append({
      "channel": channel,
      "text": text
    })

  def tearDown(self):
    self.temp_dir.cleanup()

  def test_checkpoint_is_persisted_with_state(self):
    client = FakeClient()
    self.main.process_message_event({
      "channel": "C123",
      "text": "one",
      "user": "U1",
      "ts": "1001.0001"
    }, client)

    state = self.main.load_state()
    self.assertEqual(state["current"], 1)
    self.assertEqual(state["last_user"], "U1")
    self.assertEqual(state["scores"]["U1"], 1)
    self.assertEqual(state["last_processed_ts"], "1001.0001")

  def test_catchup_replays_oldest_first(self):
    self.main.save_state({
      "current": 0,
      "last_user": "",
      "scores": {},
      "last_processed_ts": "1000.0"
    })
    client = FakeClient(history_pages=[{
      "messages": [
        {"ts": "1002.0", "text": "two", "user": "U2"},
        {"ts": "1001.0", "text": "one", "user": "U1"}
      ],
      "has_more": False
    }])

    self.main.run_catchup_if_needed(client)
    state = self.main.load_state()

    self.assertEqual(state["current"], 2)
    self.assertEqual(state["last_user"], "U2")
    self.assertEqual(state["scores"]["U1"], 1)
    self.assertEqual(state["scores"]["U2"], 1)
    self.assertEqual(
      [r["timestamp"] for r in client.reactions if r["name"] == "white_check_mark"],
      ["1001.0", "1002.0"]
    )

  def test_duplicate_events_are_ignored_after_catchup(self):
    self.main.save_state({
      "current": 2,
      "last_user": "U2",
      "scores": {"U1": 1, "U2": 1},
      "last_processed_ts": "1002.0"
    })
    client = FakeClient()

    self.main.process_message_event({
      "channel": "C123",
      "text": "two",
      "user": "U2",
      "ts": "1002.0"
    }, client)

    self.assertEqual(len(client.reactions), 0)
    self.assertEqual(self.main.load_state()["current"], 2)

  def test_first_deploy_does_not_replay_without_initial_timestamp(self):
    client = FakeClient(history_pages=[{
      "messages": [{"ts": "1001.0", "text": "one", "user": "U1"}],
      "has_more": False
    }])

    self.main.run_catchup_if_needed(client)
    self.assertEqual(client.history_calls, 0)

  def test_first_deploy_replays_when_initial_timestamp_set(self):
    os.environ["SLACK_CATCHUP_INITIAL_TIMESTAMP"] = "1000.0"
    import main
    self.main = importlib.reload(main)

    client = FakeClient(history_pages=[{
      "messages": [{"ts": "1001.0", "text": "one", "user": "U1"}],
      "has_more": False
    }])

    self.main.run_catchup_if_needed(client)
    self.assertEqual(client.history_calls, 1)
    self.assertEqual(self.main.load_state()["current"], 1)

  def test_override_command_updates_state(self):
    response = self.main.handle_override_command({
      "user_id": "U092839T3A7",
      "channel_id": "C123",
      "text": "42"
    })

    self.assertEqual(response["text"], "success")
    self.assertEqual(self.main.load_state()["current"], 42)
    self.assertEqual(len(self.posted_messages), 1)

  def test_leaderboard_command_orders_scores(self):
    self.main.save_state({
      "current": 0,
      "last_user": "",
      "scores": {"U1": 1, "U2": 5, "U3": 3},
      "last_processed_ts": None
    })

    response = self.main.handle_leaderboard_command()
    self.assertIn("1. <@U2>: 5", response["text"])
    self.assertIn("2. <@U3>: 3", response["text"])
    self.assertIn("3. <@U1>: 1", response["text"])


if __name__ == "__main__":
  unittest.main()

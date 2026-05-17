import os
import unittest

from _util import fresh_db
from ragbaz_frog import store


class AgentIdentity(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(fresh_db())
        self._env = dict(os.environ)

    def tearDown(self):
        self.conn.close()
        os.environ.clear()
        os.environ.update(self._env)

    def test_frog_agent_env_overrides_user(self):
        os.environ["FROG_AGENT"] = "claude-A"
        self.assertEqual(store.current_agent(), "claude-A")
        os.environ.pop("FROG_AGENT")
        os.environ["USER"] = "xyzzy"
        self.assertEqual(store.current_agent(), "xyzzy")

    def test_session_distinguishes(self):
        os.environ["FROG_SESSION"] = "s1"
        self.assertEqual(store.current_session(), "s1")
        os.environ.pop("FROG_SESSION")
        self.assertIn(":", store.current_session())  # host:pid

    def test_register_and_whoami(self):
        os.environ["FROG_AGENT"] = "codex-7"
        w = store.agent_whoami(self.conn)
        self.assertEqual(w["agent"], "codex-7")
        self.assertFalse(w["registered"])
        store.agent_register(self.conn, kind="llm")
        w2 = store.agent_whoami(self.conn)
        self.assertTrue(w2["registered"])
        self.assertEqual(w2["kind"], "llm")


if __name__ == "__main__":
    unittest.main()

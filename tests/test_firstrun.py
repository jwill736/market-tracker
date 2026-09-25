from market_tracker import firstrun

EXAMPLE = 'SEC_USER_AGENT="plumbline you@example.com"\nNTFY_TOPIC=\nMT_DB_PATH=market_tracker.db\n'


def test_first_run_asks_and_writes_env(tmp_path):
    (tmp_path / ".env.example").write_text(EXAMPLE)
    answers = iter(["not-an-email", "me@mail.com", "y"])
    said = []
    assert firstrun.needed(str(tmp_path))
    r = firstrun.run(str(tmp_path), ask=lambda q: next(answers), say=said.append)
    env = (tmp_path / ".env").read_text()
    assert r["created"] and r["email"] == "me@mail.com" and r["ntfy_topic"].startswith("plumbline-")
    assert 'SEC_USER_AGENT="plumbline me@mail.com"' in env and f'NTFY_TOPIC="{r["ntfy_topic"]}"' in env
    assert "MT_DB_PATH=market_tracker.db" in env and any("doesn't look like an email" in s for s in said)
    assert not firstrun.needed(str(tmp_path))


def test_second_run_keeps_answers_and_skip_works(tmp_path):
    (tmp_path / ".env.example").write_text(EXAMPLE)
    (tmp_path / ".env").write_text(EXAMPLE)                      # an old .env with the placeholder
    r = firstrun.run(str(tmp_path), ask=lambda q: "", say=lambda s: None)   # Enter skips
    assert r == {"created": False, "email": None, "ntfy_topic": None}
    assert firstrun.needed(str(tmp_path))
    r = firstrun.run(str(tmp_path), say=lambda s: None, interactive=False)
    assert r["email"] is None and "you@example.com" in (tmp_path / ".env").read_text()

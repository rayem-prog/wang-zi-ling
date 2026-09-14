from config.settings import Settings, load_settings, save_settings


def test_defaults():
    s = Settings()
    assert s.risk.max_single_position == 0.20
    assert s.engine.w_a == 0.5
    assert s.engine.w_min == 0.2
    assert s.engine.w_max == 0.8
    assert s.engine.manual_override is False
    assert s.data.index_symbol == "sh000300"
    assert s.account.cash == 100_000.0


def test_roundtrip(tmp_path):
    path = str(tmp_path / "user_settings.json")
    s = Settings()
    save_settings(s, path)
    s2 = load_settings(path)
    assert s2.risk.stop_loss == s.risk.stop_loss
    assert s2.account.max_positions == s.account.max_positions


def test_missing_file_returns_default(tmp_path):
    path = str(tmp_path / "nope.json")
    s = load_settings(path)
    assert s.engine.w_a == 0.5

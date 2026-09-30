from bqtop.wizard import _list, _toml_list, _valid_tz


def test_valid_tz():
    assert _valid_tz("Europe/Brussels") and _valid_tz("UTC")
    assert not _valid_tz("today") and not _valid_tz("")


def test_lists():
    assert _list(" us, eu ,") == ["us", "eu"]
    assert _toml_list(["a", "b"]) == '["a", "b"]'

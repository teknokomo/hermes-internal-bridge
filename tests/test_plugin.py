"""Side-channel sender and per-profile identity under a multiplexed gateway."""

from types import SimpleNamespace

from hermes_internal_bridge.plugin import GatewayMessageSender, _profile_from_context


def test_thread_metadata_passed_for_topic_address():
    assert GatewayMessageSender._thread_metadata({"thread_id": "t-9"}) == {"thread_id": "t-9"}


def test_thread_metadata_absent_without_topic():
    assert GatewayMessageSender._thread_metadata({"thread_id": ""}) is None


def test_profile_from_context_uses_manager_home(tmp_path):
    """Every profile has its own PluginManager home; one process serves them all."""
    ctx = SimpleNamespace(_manager=SimpleNamespace(home_path=tmp_path / "profiles" / "hib-test"))
    assert _profile_from_context(ctx) == "hib-test"


def test_profile_from_context_default_for_root_home(tmp_path):
    ctx = SimpleNamespace(_manager=SimpleNamespace(home_path=tmp_path))
    assert _profile_from_context(ctx) == "default"


def test_profile_from_context_falls_back_without_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "coder"))
    assert _profile_from_context(SimpleNamespace()) == "coder"

"""Root Hermes home resolution (shared-root rule): two cases proven by tests."""

from hermes_internal_bridge import paths


def _prep_root(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("", encoding="utf-8")  # home marker
    return root


def test_named_profile_resolves_to_root(tmp_path, monkeypatch):
    root = _prep_root(tmp_path / "hermes-root")
    profile = root / "profiles" / "coder"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    assert paths.resolve_hermes_root() == root
    assert paths.profile_name() == "coder"


def test_default_profile_home_is_root_itself(tmp_path, monkeypatch):
    root = _prep_root(tmp_path / "hermes-root")
    monkeypatch.setenv("HERMES_HOME", str(root))
    assert paths.resolve_hermes_root() == root
    assert paths.profile_name() == "default"


def test_profile_name_resolved_from_plugin_path_in_multiplex(tmp_path):
    """One multiplexed process serves several profiles with the same HERMES_HOME;
    the plugin copy's own location must win over the environment."""
    module = tmp_path / "profiles" / "hib-test" / "plugins" / "hermes-internal-bridge" / "hermes_internal_bridge" / "paths.py"
    assert paths.profile_name(module_path=module, env={"HERMES_HOME": str(tmp_path)}) == "hib-test"


def test_profile_name_default_for_root_copy(tmp_path):
    module = tmp_path / "plugins" / "hermes-internal-bridge" / "hermes_internal_bridge" / "paths.py"
    assert paths.profile_name(module_path=module, env={"HERMES_HOME": str(tmp_path)}) == "default"


def test_two_profiles_share_one_root(tmp_path, monkeypatch):
    """The shared bus must land on the same database for every profile of a host."""
    root = _prep_root(tmp_path / "hermes-root")
    for name in ("alpha", "beta"):
        (root / "profiles" / name).mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "alpha"))
    first = paths.resolve_hermes_root()
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "beta"))
    second = paths.resolve_hermes_root()
    assert first == second == root


def test_default_bus_state_path_is_on_root_plugin_data(tmp_path, monkeypatch):
    root = _prep_root(tmp_path / "hermes-root")
    (root / "profiles" / "coder").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "coder"))
    state_path = paths.default_bus_state_path()
    assert state_path == root / "plugin-data" / "hermes-internal-bridge" / "hib.sqlite3"


def test_expand_path_supports_hermes_root_placeholder(tmp_path, monkeypatch):
    root = _prep_root(tmp_path / "hermes-root")
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "coder"))
    expanded = paths.expand_path("${HERMES_ROOT}/plugin-data/x.db")
    assert expanded == root / "plugin-data" / "x.db"


def test_platform_default_home(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    home = paths.platform_default_home({})
    if paths.sys.platform == "win32":
        assert home.name == "hermes"
    else:
        assert home.name == ".hermes"

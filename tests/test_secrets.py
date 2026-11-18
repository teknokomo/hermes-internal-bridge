"""Secret filter (FR-043): every pattern must be caught before a ledger/log write."""

from hermes_internal_bridge.secrets import find_secrets, redact, scrub


def test_openai_style_key():
    assert find_secrets("key sk-abc123DEF456ghi789JKL")[0].startswith("sk-")


def test_telegram_bot_token():
    assert find_secrets("123456789:AAEchB_xXyYzZ1234567890abcdefghi") != []


def test_github_token():
    assert find_secrets("ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4") != []


def test_aws_key():
    assert find_secrets("AKIA" + "IOSFODNN7EXAMPLE") != []


def test_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert find_secrets(jwt) != []


def test_key_value_pairs():
    assert find_secrets("api_key = super-secret-value") != []
    assert find_secrets("PASSWORD:hunter2") != []


def test_authorization_header():
    assert find_secrets("Authorization: Bearer abc.def.ghi-jkl-123456") != []


def test_clean_text_has_no_match():
    assert find_secrets("Обычное сообщение про погоду и планы на неделю") == []


def test_redact_replaces_all_matches():
    text = "токен sk-abc123DEF456ghi789JKL и ещё sk-xyz999YYY888www777VVV"
    redacted = redact(text)
    assert "sk-" not in redacted
    assert "[REDACTED]" in redacted


def test_scrub_deep_structure():
    details = {"env": ["API_KEY=sk-abc123DEF456ghi789JKL"], "note": "чисто", "nested": {"token": "ghp_abcdef123456ghijklmn"}}
    scrubbed = scrub(details)
    assert "sk-" not in scrubbed["env"][0]
    assert scrubbed["note"] == "чисто"
    assert "ghp_" not in scrubbed["nested"]["token"]

from pathlib import Path


def test_example_env_contains_names_not_credentials() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "GEMINI_API_KEY=" in text
    assert "TELEGRAM_BOT_TOKEN=" in text
    assert "AIza" not in text
    assert ":AA" not in text


def test_state_is_not_committed_to_the_public_code_repository() -> None:
    ignored = Path(".gitignore").read_text(encoding="utf-8").splitlines()

    assert "state/" in ignored
    assert "state-repo/" in ignored

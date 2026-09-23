from pathlib import Path


WORKFLOW = Path(".github/workflows/news-bot.yml")
KEEPALIVE = Path(".github/workflows/keepalive.yml")


def test_workflow_has_safety_controls() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "7,22,37,52 * * * *"' in text
    assert "workflow_dispatch:" in text
    assert "contents: read" in text
    assert "cancel-in-progress: false" in text
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in text
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in text
    assert "GEMINI_MODELS: ${{ vars.GEMINI_MODELS || '' }}" in text
    assert "GEMINI_MODEL:" not in text
    assert "TELEGRAM_CHAT_ID" not in text
    assert "uses: actions/checkout@v7" in text
    assert "uses: actions/setup-python@v7" in text
    assert 'pip install -e ".[dev]"' in text
    assert "python -m pytest -q" in text
    assert "chore: update delivery state" in text


def test_state_lives_in_private_repository_checked_out_with_deploy_key() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    state_block = text.split("- name: Check out private state", 1)[1].split("- name:", 1)[0]

    assert "repository: ${{ vars.STATE_REPOSITORY }}" in state_block
    assert "ref: ${{ vars.STATE_BRANCH }}" in state_block
    assert "ssh-key: ${{ secrets.STATE_DEPLOY_KEY }}" in state_block
    assert "path: state-repo" in state_block
    assert "STATE_PATH: state-repo/state/seen.json" in text
    persist = text.split("- name: Persist delivery state", 1)[1]
    assert "cd state-repo" in persist
    assert "git push" in persist


def test_manual_runs_test_but_scheduled_runs_do_not() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    test_step = text.split("- name: Run tests", 1)[1].split("- name:", 1)[0]

    assert "if: github.event_name != 'schedule'" in test_step


def test_keepalive_commits_monthly_so_public_schedule_stays_enabled() -> None:
    text = KEEPALIVE.read_text(encoding="utf-8")

    assert "schedule:" in text
    assert "contents: write" in text
    assert "git commit --allow-empty" in text
    assert "git push" in text

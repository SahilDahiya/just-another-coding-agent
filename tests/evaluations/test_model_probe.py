from pathlib import Path

from evaluations.model_probe import (
    LEGACY_OPENAI_MODEL_PROBE_IDS,
    default_probe_targets,
    infer_probe_target,
    load_dotenv_file,
    missing_credentials_by_lane,
    select_probe_targets,
)


def test_default_probe_targets_include_shipped_and_legacy_openai_candidates() -> None:
    targets = default_probe_targets()
    model_ids = [target.model_id for target in targets]

    assert "openai-responses:gpt-5.4" in model_ids
    assert "openai-responses:gpt-5.4-chatgpt" in model_ids
    assert "anthropic:claude-sonnet-4-5" in model_ids
    assert list(LEGACY_OPENAI_MODEL_PROBE_IDS) == [
        "openai-responses:gpt-5-codex",
        "openai-responses:gpt-5-chatgpt",
        "openai-responses:gpt-5-mini-chatgpt",
    ]
    for legacy_model_id in LEGACY_OPENAI_MODEL_PROBE_IDS:
        assert legacy_model_id in model_ids


def test_default_probe_targets_can_skip_legacy_candidates() -> None:
    model_ids = [target.model_id for target in default_probe_targets(shipped_only=True)]

    for legacy_model_id in LEGACY_OPENAI_MODEL_PROBE_IDS:
        assert legacy_model_id not in model_ids


def test_infer_probe_target_classifies_openai_oauth_and_api_models() -> None:
    oauth_target = infer_probe_target(
        "openai-responses:gpt-5.4-chatgpt",
        source="cli",
    )
    api_target = infer_probe_target(
        "openai-responses:gpt-5.4",
        source="cli",
    )

    assert oauth_target.lane == "openai-oauth"
    assert oauth_target.provider_model_name == "gpt-5.4"
    assert api_target.lane == "openai-api"
    assert api_target.provider_model_name == "gpt-5.4"


def test_load_dotenv_file_sets_missing_values_without_overwriting(
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OPENAI_API_KEY=from-dotenv\n"
        "export ANTHROPIC_API_KEY='anthropic-secret'\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    environ = {
        "OPENAI_API_KEY": "already-set",
    }

    load_dotenv_file(dotenv_path, environ=environ)

    assert environ == {
        "OPENAI_API_KEY": "already-set",
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "EMPTY": "",
    }


def test_select_probe_targets_filters_to_requested_lane() -> None:
    targets = select_probe_targets(
        lane_filters={"anthropic-api"},
        explicit_models=[],
        shipped_only=False,
    )

    assert targets
    assert {target.lane for target in targets} == {"anthropic-api"}


def test_missing_credentials_by_lane_reports_each_missing_secret(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "evaluations.model_probe.resolve_openai_codex_oauth_credentials_sync",
        lambda: None,
    )
    monkeypatch.setattr(
        "evaluations.model_probe.resolve_provider_secret",
        lambda provider: None,
    )
    targets = (
        infer_probe_target("openai-responses:gpt-5.4", source="cli"),
        infer_probe_target("openai-responses:gpt-5.4-chatgpt", source="cli"),
        infer_probe_target("anthropic:claude-sonnet-4-5", source="cli"),
    )

    missing = missing_credentials_by_lane(targets)

    assert set(missing) == {"openai-api", "openai-oauth", "anthropic-api"}

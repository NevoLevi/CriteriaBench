from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_wrapper_pins_project_file_and_disables_env_discovery() -> None:
    wrapper = (ROOT / "scripts" / "compose-safe.ps1").read_text(encoding="utf-8")

    assert '$env:COMPOSE_DISABLE_ENV_FILE = "1"' in wrapper
    assert 'SetEnvironmentVariable("COMPOSE_ENV_FILES", $null, "Process")' in wrapper
    assert 'SetEnvironmentVariable("COMPOSE_FILE", $null, "Process")' in wrapper
    assert "docker compose --file $composePath --project-name criteriabench" in wrapper
    assert '$_ -like "--env-file=*"' in wrapper
    assert '$_ -like "--file=*"' in wrapper
    assert '$_ -like "-f=*"' in wrapper
    assert "env_file" not in (ROOT / "compose.yaml").read_text(encoding="utf-8")


def test_compose_wrapper_restores_the_callers_environment() -> None:
    wrapper = (ROOT / "scripts" / "compose-safe.ps1").read_text(encoding="utf-8")

    assert '"COMPOSE_DISABLE_ENV_FILE", $previousDisableEnvFile, "Process"' in wrapper
    assert '"COMPOSE_ENV_FILES", $previousComposeEnvFiles, "Process"' in wrapper
    assert '"COMPOSE_FILE", $previousComposeFile, "Process"' in wrapper


def test_ci_disables_implicit_dotenv_discovery() -> None:
    for workflow in ("ci.yml", "publish.yml"):
        content = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert 'COMPOSE_DISABLE_ENV_FILE: "1"' in content
        assert 'COMPOSE_ENV_FILES: ""' in content

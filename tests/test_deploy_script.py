from pathlib import Path


def test_deploy_preserves_runtime_env_variants_and_local_venv():
    script = Path("scripts/deploy.sh").read_text()

    include_example = script.index("--include='.env.example'")
    exclude_env_variants = script.index("--exclude='.env*'")
    assert include_example < exclude_env_variants

    for required in (
        "--exclude='.venv/'",
        "--exclude='data/'",
        "--exclude='docker-compose.override.yml'",
    ):
        assert required in script

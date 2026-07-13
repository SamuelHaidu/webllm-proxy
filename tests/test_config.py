"""utils.config: YAML + pydantic parsing."""

from webllm_proxy.utils.config import Config, load_config


def test_load_sample_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """
server:
  host: 0.0.0.0
  port: 6000
providers:
  chatgpt:
    enabled: true
  databricks:
    enabled: true
    workspace_url: "https://x.databricks.com/?o=123"
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.server.port == 6000
    assert cfg.enabled_providers() == ["chatgpt", "databricks"]
    assert cfg.providers.databricks.workspace_url.endswith("o=123")


def test_defaults_when_empty(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.server.port == 5100
    assert cfg.enabled_providers() == []


def test_profile_dir_override(tmp_path):
    cfg = Config.model_validate({"providers": {"chatgpt": {"profile_dir": "/tmp/x"}}})
    assert str(cfg.profile_dir("chatgpt")) == "/tmp/x"

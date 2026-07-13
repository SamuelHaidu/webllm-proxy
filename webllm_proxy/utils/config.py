"""Config for the unified server: parse `webllm-proxy.yaml` with pyyaml and
validate/type it with pydantic. One server, one port, N enabled providers."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from . import env


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5100


class ChatgptConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    profile_dir: str | None = None


class DatabricksConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    workspace_url: str = ""
    profile_dir: str | None = None
    style_rules: bool = True


class CopilotConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    edition: str = "m365"
    profile_dir: str | None = None
    url: str | None = None


class ProvidersConfig(BaseModel):
    chatgpt: ChatgptConfig = Field(default_factory=ChatgptConfig)
    databricks: DatabricksConfig = Field(default_factory=DatabricksConfig)
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    def enabled_providers(self) -> list[str]:
        out = []
        for name in ("chatgpt", "databricks", "copilot"):
            if getattr(self.providers, name).enabled:
                out.append(name)
        return out

    def profile_dir(self, provider: str) -> Path:
        pc = getattr(self.providers, provider)
        override = getattr(pc, "profile_dir", None)
        if override:
            return Path(override)
        return env.data_dir(f"{provider}-proxy") / "profile"


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)

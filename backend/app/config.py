from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        extra="ignore",
        protected_namespaces=(),
    )

    database_url: str = f"sqlite:///{ROOT / 'data' / 'safenest.db'}"
    ollama_url: str = "http://localhost:11434"
    model_adapter: str = "ollama"  # ollama | mock
    notifier: str = "log"  # log | aws
    enable_cloud_deploy: bool = False
    escalation_timeout_sec: int = 20
    house_tz: str = "America/Los_Angeles"
    notify_fail_channels: str = ""  # e.g. "push" or "push,sms,email" to demo fallback
    deploy_step_delay_sec: float = 1.0  # makes Deploying visible in the UI
    warmup_timeout_sec: float = 90.0
    mock_latency_ms: int = 60  # the mock adapter pretends to take this long
    classifier_timeout_sec: float = 8.0
    prompt_version: str = "v1"
    merge_window_min: int = 10
    offline_minutes: int = 10
    fast_forward_device: bool = True
    clip_secret: str = "safenest-demo-clip-secret"
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()

# Mutable demo switches flipped from the Operations page.
runtime = {"ai_outage": False}

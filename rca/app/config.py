from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    prometheus_url: str = "http://prometheus:9090"
    poll_interval_seconds: int = 5
    # Spike windows used by metric summaries.
    rate_window: str = "1m"


settings = Settings()

"""Operational knobs of run execution, streaming and maintenance (env: RUN_TIMEOUT_SECONDS, CHAT_WAIT_SECONDS, ...)."""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.application.use_cases.orchestrator import ExecutionLimits


class RunSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=('../.env', '.env'), extra='ignore')
    run_max_depth: int = Field(3, ge=1)
    run_max_delegations: int = Field(8, ge=0)
    run_max_tool_calls: int = Field(12, ge=1)
    run_timeout_seconds: float = Field(120, gt=0)
    run_max_output_tokens: int | None = Field(16000, ge=1)
    max_active_runs: int | None = Field(20, ge=1)  # per workspace
    chat_wait_seconds: float = Field(90, ge=0)  # POST /chat waits this long before answering 202
    cancel_wait_seconds: float = Field(2, ge=0)
    run_lease_seconds: float = Field(30, gt=0)
    run_heartbeat_seconds: float = Field(3, gt=0)  # also the latency of cross-instance cancellation
    maintenance_interval_seconds: float = Field(10, gt=0)
    sse_heartbeat_seconds: float = Field(15, gt=0)
    sse_poll_seconds: float = Field(0.5, gt=0)
    event_retention_days: int = Field(7, ge=1)

    def limits(self) -> ExecutionLimits:
        return ExecutionLimits(self.run_max_depth, self.run_max_delegations, self.run_max_tool_calls,
                               self.run_timeout_seconds, self.run_max_output_tokens)


@lru_cache
def get_run_settings() -> RunSettings:
    return RunSettings()

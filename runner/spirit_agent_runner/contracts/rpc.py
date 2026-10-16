from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **({"data": self.data} if self.data else {})}


class SkillScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(gt=0, strict=True)
    system_preset_id: Literal["companion", "copywriter", "language_teacher"]


class ToolExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, strict=True)
    args: dict[str, JsonValue] = Field(default_factory=dict)
    call_id: str | None = Field(default=None, min_length=1)
    skill_scope: SkillScopeRequest | None = None


class ConfigReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, JsonValue]


class CallGetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, strict=True)


class CallCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    req_id: str | int | None = None


class RunnerEmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunnerToolSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    description: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class RunnerToolResult(RootModel[JsonValue]):
    pass


class RunnerToolsResult(BaseModel):
    tools: list[RunnerToolSchema]


class RunnerConfigResult(BaseModel):
    ok: Literal[True] = True


class RunnerCallResult(BaseModel):
    call_id: str
    status: Literal["claimed", "completed", "failed", "unknown", "not_found"]
    result: JsonValue = None
    error: str | None = None
    claimed_at: int | float | None = None
    finished_at: int | float | None = None


class RunnerCancelResult(BaseModel):
    ok: bool
    error: Literal["no_in_flight"] | None = None


class RunnerCapabilitiesPayload(BaseModel):
    microphone: bool | None = None
    screen_capture: bool | None = None
    system_activity: bool | None = None
    platform: str | None = None
    python: str | None = None


class RunnerCapabilityHealth(BaseModel):
    available: bool
    reason: str | None = None


class RunnerReadyPayload(BaseModel):
    version: str
    run_generation: str
    pid: int
    capabilities: RunnerCapabilitiesPayload
    capabilities_health: dict[str, RunnerCapabilityHealth]
    probe_failed: bool
    reconnect_streak: int


class RunnerCapabilitiesChangedPayload(BaseModel):
    run_generation: str
    capabilities: RunnerCapabilitiesPayload
    probe_failed: bool


class RunnerSystemInfo(BaseModel):
    platform: str
    python: str
    release: str
    machine: str


class RunnerInfoResult(RunnerReadyPayload):
    started_at: float
    uptime_seconds: float
    reconnect_count: int
    tool_count: int
    network_reachable: bool
    disk_free_bytes: int | None
    system: RunnerSystemInfo


@dataclass(frozen=True, slots=True)
class RunnerRpcContract:
    request: type[BaseModel]
    response: type[BaseModel]


RUNNER_CONTRACTS = {
    "tools.execute": RunnerRpcContract(ToolExecuteRequest, RunnerToolResult),
    "tools.list": RunnerRpcContract(RunnerEmptyRequest, RunnerToolsResult),
    "config.replace": RunnerRpcContract(ConfigReplaceRequest, RunnerConfigResult),
    "runner.info": RunnerRpcContract(RunnerEmptyRequest, RunnerInfoResult),
    "calls.get": RunnerRpcContract(CallGetRequest, RunnerCallResult),
    "calls.cancel": RunnerRpcContract(CallCancelRequest, RunnerCancelResult),
}

RUNNER_EVENTS = {
    "runner.ready": RunnerReadyPayload,
    "runner.capabilities_changed": RunnerCapabilitiesChangedPayload,
}

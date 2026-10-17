"""连接控制与本机设备通信的输入、结果和权限。"""

from dataclasses import dataclass
from typing import Literal

from features.companion.intent_contracts import CompanionSignal
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from runtime.conversation import ToolsSyncResult


class RpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(RpcRequest):
    pass


class EmptyResult(BaseModel):
    pass


class GatewayResumeRequest(RpcRequest):
    stream_id: str | None = None
    last_seq: int | None = Field(default=None, ge=0)


class GatewayResumeResult(BaseModel):
    stream_id: str
    current_seq: int
    recovery: Literal["replayed", "snapshot_required"]
    replayed_count: int
    pending_calls: list[str]


class GatewayAckRequest(RpcRequest):
    seq: int = Field(ge=0)


class GatewayAckResult(BaseModel):
    acked: int
    pruned: int


class RunnerToolSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ToolsSyncRequest(RpcRequest):
    skill_scope_version: Literal[1]
    tools: list[RunnerToolSchema]


class ToolResultRequest(RpcRequest):
    call_id: str = Field(min_length=1)
    result: JsonValue


class ToolResultResult(BaseModel):
    status: Literal["accepted", "no_longer_pending"]


class CompanionSignalResult(BaseModel):
    queued: bool


@dataclass(frozen=True)
class RpcContract:
    request: type[BaseModel]
    response: type[BaseModel]
    remote: bool = False
    concurrent: bool = False
    wait_for_model: bool = False


RPC_CONTRACTS: dict[str, RpcContract] = {
    "gateway.resume": RpcContract(GatewayResumeRequest, GatewayResumeResult, remote=True),
    "gateway.ack": RpcContract(GatewayAckRequest, GatewayAckResult, remote=True, concurrent=True),
    "gateway.ping": RpcContract(EmptyRequest, EmptyResult, remote=True, concurrent=True),
    "tools.sync": RpcContract(ToolsSyncRequest, ToolsSyncResult, concurrent=True),
    "tool.result": RpcContract(ToolResultRequest, ToolResultResult, concurrent=True),
    "companion.signal": RpcContract(CompanionSignal, CompanionSignalResult),
}

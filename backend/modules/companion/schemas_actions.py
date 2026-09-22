"""动作库跨边界 schema：工具入参、REST 响应、播放指令与回执。

source / user_id / 预算日 / 系统槽位由服务端绑定。时长上限由策略层按后台配置校验；
schema 只守绝对上限 12 秒。
"""

from pydantic import BaseModel, ConfigDict, Field

ABSOLUTE_MAX_DURATION_SECONDS = 12.0


class ActionDesignRequest(BaseModel):
    """LLM action_design 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=400)
    duration_seconds: float = Field(gt=0, le=ABSOLUTE_MAX_DURATION_SECONDS)
    clip_kind: str = Field(pattern="^(loop|once)$")
    expected_pack_id: int | None = None


class ActionDesignResult(BaseModel):
    """action_design 受理结果：reused / pending_review / rejected。"""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(reused|pending_review|rejected)$")
    proposal_id: int | None = None
    action_id: int | None = None
    message: str = ""


class ActionSearchHit(BaseModel):
    """action_search 单条命中。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    key: str
    name: str
    system_slot: str = ""
    kind: str = "once"
    motion_description: str = ""
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    loopable: bool = False


class ActionSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[ActionSearchHit] = Field(default_factory=list)
    total: int = 0


class ActionInspectResponse(BaseModel):
    """action_inspect 状态视图。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(proposal|action)$")
    id: int
    status: str
    stage: str = ""
    action_id: int | None = None
    message: str = ""
    error: str | None = None


class ActionPlayRequest(BaseModel):
    """LLM action_play 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    reason: str = Field(default="", max_length=200)
    expected_pack_id: int | None = None


class ActionPlayResult(BaseModel):
    """action_play 结果：queued 不等于 completed。"""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(queued|rejected)$")
    play_id: str = ""
    message: str = ""


class ActionPlaybackReceipt(BaseModel):
    """客户端播放回执：按 play_id 幂等聚合。"""

    model_config = ConfigDict(extra="forbid")

    play_id: str
    status: str = Field(pattern="^(started|completed|interrupted|rejected)$")
    visible_duration_ms: int = 0
    error: str | None = None


class ActionPlayCommand(BaseModel):
    """companion.action.play_requested 载荷。"""

    model_config = ConfigDict(extra="forbid")

    play_id: str
    target_device: str = ""
    target_surface: str = ""
    pack_id: int
    appearance_epoch: int
    action_id: int
    asset_revision_id: int | None = None
    repeat_count: int = Field(default=1, ge=1, le=5)
    expires_at: str | None = None
    source: str = "chat_expression"


class ActionSummary(BaseModel):
    """动作元信息摘要（catalog API / 提示词资料块共用）。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    key: str
    name: str
    system_slot: str = ""
    kind: str = "once"
    motion_description: str = ""
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    loopable: bool = False
    enabled: bool = True


class ActionCatalogResponse(BaseModel):
    """动作目录列表（按包隔离）。"""

    model_config = ConfigDict(extra="forbid")

    pack_id: int
    catalog_version: int
    manifest_url: str | None = None
    actions: list[ActionSummary] = Field(default_factory=list)


class ActionProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: int
    pack_id: int
    source: str
    name: str
    status: str
    review_decision: str | None = None
    review_reason: str | None = None
    action_id: int | None = None
    created_at: str


class ActionBudgetStatus(BaseModel):
    """当前用户动作制作额度状态（按用户本地日）。
    上限实际值由后台配置给出，schema 不背书默认。"""

    model_config = ConfigDict(extra="forbid")

    budget_date: str
    autonomous_create_used: int = 0
    autonomous_create_limit: int = 0
    user_requested_create_used: int = 0
    user_requested_create_limit: int = 0

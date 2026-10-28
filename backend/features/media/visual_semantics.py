"""生活与视觉共享的有限语义词表；不包含状态或选择逻辑。"""

from typing import Annotated, Literal, get_args

from pydantic import Field

ActivityType = Literal["idle", "reading", "working", "creating", "thinking", "resting", "organizing", "socializing"]
SemanticTag = Literal[
    "idle",
    "attention",
    "interaction",
    "reading",
    "working",
    "creating",
    "thinking",
    "resting",
    "organizing",
    "socializing",
    "stretching",
    "grooming",
    "drinking",
    "dancing",
]
SceneTag = Literal["indoor", "outdoor", "desk", "seat", "bed", "open_space", "book", "computer", "cup", "tools"]
SemanticTags = Annotated[list[SemanticTag], Field(max_length=8)]
SceneTags = Annotated[list[SceneTag], Field(max_length=10)]
ActionPerformanceRole = Literal["idle", "sustained", "reaction"]
ActionMotionIntensity = Literal["subtle", "normal", "expressive"]
ACTION_SEMANTIC_TAGS = frozenset(get_args(SemanticTag))

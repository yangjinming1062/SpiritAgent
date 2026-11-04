"""环境生成的冻结输入与图片链；新建与重制共用同一结构与恢复读取。"""

from infrastructure.ai.selection import ProviderSelection
from pydantic import BaseModel, Field

from features.media.character_images import ImageChainState
from features.scenes.models import SceneJob
from features.scenes.schemas import SceneImageDimensions, SceneImageSize


class SceneGenerationState(BaseModel):
    prompt: str = ""
    target_size: SceneImageSize | None = None
    image_chain: ImageChainState = Field(default_factory=ImageChainState)
    wallpaper_path: str = ""
    source_size: SceneImageDimensions | None = None
    selection: ProviderSelection | None = None


def scene_generation_state(job: SceneJob | None) -> SceneGenerationState:
    return SceneGenerationState.model_validate((job.checkpoint if job is not None else None) or {})


def scene_chain_paths(job: SceneJob | None) -> set[str]:
    return scene_generation_state(job).image_chain.stored_paths()

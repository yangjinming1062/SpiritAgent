from typing import Literal

PresentationMode = Literal["desktop", "window"]
VisualSurface = Literal["desktop", "life", "sprite", "none"]
PlayableSurface = Literal["desktop", "life", "sprite"]


def visual_surface_accepts(surface: VisualSurface, asset_kind: str) -> bool:
    if asset_kind == "actor":
        return surface == "sprite"
    return asset_kind in ("scene", "cinematic") and surface in ("desktop", "life")

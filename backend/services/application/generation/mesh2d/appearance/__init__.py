"""原图锁定分层重建：把 see-through 候选 PSD 的可见区域回填为原图像素。

职责与背景见本目录 README。本包不依赖 backend 运行时（components/SETTINGS），
仅使用 PIL / numpy / psd-tools；编排入口在 pipeline，诊断产物由 trace 落盘。
"""

from .gate import GateReport, run_gate
from .rebuild import AppearanceError, RebuiltAppearance, rebuild_source_locked
from .source_asset import SourceAppearance, load_source_appearance

__all__ = [
    "AppearanceError",
    "GateReport",
    "RebuiltAppearance",
    "SourceAppearance",
    "load_source_appearance",
    "rebuild_source_locked",
    "run_gate",
]

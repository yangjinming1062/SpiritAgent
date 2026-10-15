# 扫描件与复杂版式

仅在扫描件缺少文本层，或轻量提取不能保留所需版式、公式时使用 Marker。不要用它代替 `officecli` 处理 Office 文件。

## 准备环境

Marker 依赖 PyTorch 和模型，可能占用数 GB；首次运行可能联网下载模型，CPU 处理也可能较慢。先核对当前 Python/平台是否受所用版本支持，以及环境目录和模型缓存所在盘的空间、网络与可用算力。不要把这些依赖安装进 Runner 环境。

从 `skill_view` 返回的 `skill_dir` 解析 `scripts/extract_marker.py` 的绝对路径。脚本的 `--check` 可作初步磁盘检查，但只检查根路径所在盘，不能证明环境目录和模型缓存都有空间。

uv 可用时用临时环境执行；将占位路径替换为实际绝对路径：

```sh
uv run --no-project --with marker-pdf python "<skill_dir>/scripts/extract_marker.py" "/absolute/scanned.pdf"
```

uv 不可用时，用 `python -m venv <env_dir>` 建立任务独立环境，再使用其中的 Python 执行 `-m pip install marker-pdf` 和脚本。Windows 解释器为 `<env_dir>/Scripts/python.exe`，macOS/Linux 为 `<env_dir>/bin/python`。依赖或模型下载失败时保留错误，并报告尚未完成 OCR；PyMuPDF 不能从扫描图像恢复文字。

## 输出与核对

- 默认输出 Markdown 到标准输出；`--json` 输出包含 `markdown` 和可用 `metadata` 的 JSON，不代表完整的结构化页面数据。
- `--output_dir /absolute/output_dir` 仅用于保存结果中的图像，正文仍由标准输出返回。需要正文文件时由调用方保存。
- `--use_llm` 仅在已配置所需模型服务、凭据且当前任务授权覆盖文档外发时使用；默认提取不启用该参数。
- 先处理或抽查有代表性的页面，核对页数、段落顺序、公式和表格中的关键数字。OCR 结果可能有误，保留无法确认的内容及对应页码。

以实际进程退出状态、返回正文和生成文件判断成功；不要依据安装完成或文件生成就声称识别准确。

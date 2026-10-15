# 文本 PDF 与轻量提取

从 `skill_view` 返回的 `skill_dir` 解析 `scripts/extract_pymupdf.py` 的绝对路径。脚本不做 OCR；提取结果为空或阅读顺序不可靠时，按需读取 `references/ocr.md`，不要将空结果当作没有内容。

## 执行环境

先检查可用 Python、uv 和依赖。可使用 uv 的临时环境，不改动 Runner 环境；以下 `<skill_dir>` 和输入路径均替换为实际绝对路径：

```sh
uv run --no-project --with pymupdf python "<skill_dir>/scripts/extract_pymupdf.py" "/absolute/document.pdf"
```

若 uv 不可用，在任务工作目录用 `python -m venv <env_dir>` 创建独立环境，再用该环境的 Python 执行 `-m pip install pymupdf` 和脚本。Windows 解释器位于 `<env_dir>/Scripts/python.exe`，macOS/Linux 位于 `<env_dir>/bin/python`；不依赖激活环境或全局 `pip`。

## 按任务选择参数

每次选择一种输出模式，使用脚本 `--help` 核对用法：

| 任务 | 参数与依赖 |
|---|---|
| 普通文本 | 无额外参数，只需 `pymupdf` |
| Markdown | `--markdown`；另需 `pymupdf4llm` |
| 页码筛选 | `--pages 0-4` 或 `--pages 2`；0 起始、区间含末页，仅适用于普通文本和 Markdown |
| 表格 | `--tables`；另需 `pandas`、`tabulate`，逐页输出识别到的表格 |
| 内嵌图像 | `--images /absolute/output_dir`；保存图像，不识别图像中的文字 |
| 元数据 | `--metadata`；输出页数、标题和作者等 JSON |

uv 调用按需增加 `--with pymupdf4llm`，或 `--with pandas --with tabulate`；独立 venv 使用其解释器安装对应包。文本、Markdown、表格和元数据写到标准输出，需要文件时由调用方保存；图像目录不是正文输出路径。

本地处理前确认输入文件存在、可读取。先检查少量代表页，再处理完整文档；若要引用原文页码，将脚本的 0 起始索引转换为用户看到的 1 起始页码。

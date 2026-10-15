---
name: ocr-and-documents
description: "Extract text from PDFs and scanned documents; use OCR when pages lack usable text or need layout analysis."
version: 2.3.0
author: SpiritAgent Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  spiritagent:
    tags: [PDF, Documents, Text-Extraction, OCR]
    related_skills: [officecli]
---

# PDF 与扫描件提取

Word、Excel、PowerPoint 文件交给 `officecli`。本技能处理 PDF 和扫描件的文本提取。

按文档来源和已知文本层选择当前需要的参考：

- **文本 PDF，或类型未知、需要轻量检查：** 优先用 PyMuPDF，读取 `skill_view(name="ocr-and-documents", file_path="references/extraction.md")`；页码筛选、表格与内嵌图像提取也见此参考。
- **明确没有文本层的扫描件，或复杂版式、公式需要 OCR：** 读取 `skill_view(name="ocr-and-documents", file_path="references/ocr.md")`，核对并准备 Marker 的重依赖环境。
- **远程 URL，且当前确有 `web_extract`：** 可尝试远程提取。检查返回是否包含所需正文、页数与内容范围；抓取到落地页、摘要或空文本不算完成提取，再根据文档情况选择本地提取参考。

脚本和参考文件的路径以本次 `skill_view` 返回的 `skill_dir` 为根解析；使用绝对路径执行脚本，不假定当前目录位于技能目录，也不假定 Runner 已安装提取依赖。

提取后抽查关键页、段落顺序、数字与表格。缺页、乱码或 OCR 不确定处如实标明；仅保存或交付实际生成的内容。

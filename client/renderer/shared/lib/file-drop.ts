/**
 * 从拖拽或剪贴板 FileList/File[] 中解析真实文件系统路径（Electron 环境下通过 webUtils 获取）。
 * 仅保留存在有效文件路径的条目，避免将无法解析的 blob/dataURL 注入路径管道。
 * 白名单注册由 preload 的 getPathForFile 内部完成，渲染层不再自授。
 */
export function resolveDroppedFiles(fileList: FileList | File[] | null | undefined): string[] {
  const files = Array.from(fileList ?? [])

  if (files.length === 0) {
    return []
  }

  const webUtils = window.spiritagentWebUtils

  if (!webUtils) {
    return []
  }

  const paths: string[] = []

  for (const f of files) {
    try {
      const p = webUtils.getPathForFile(f)

      if (p) {
        paths.push(p)
      }
    } catch {
      /* 单个文件解析失败不影响其他文件 */
    }
  }

  return paths
}

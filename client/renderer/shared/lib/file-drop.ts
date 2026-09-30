/** 从拖拽或剪贴板 FileList/File[] 解析真实文件系统路径（Electron webUtils）；仅保留有效路径条目，避免把无法解析的 blob/dataURL 注入路径管道。白名单由 preload 的 getPathForFile 内部完成。 */
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

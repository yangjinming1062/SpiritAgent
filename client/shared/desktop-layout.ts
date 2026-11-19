export const DESKTOP_LAYOUT = {
  bottomInset: 96,
  contentInset: 12,
  barHeight: 64,
  barBottom: 12,
  avatarSize: 76,
  controlSize: 44,
  controlEdge: 16,
  minRailDisplayWidth: 412,
  wingScrollSize: 20,
  wingScrollGap: 4,
  wingScrollbarGutter: 12
} as const

/** 与桌面底栏的响应式尺寸保持一致；每翼至少容纳一个可操作图标。 */
export function getDesktopRailLayout(viewportWidth: number) {
  const compact = viewportWidth <= 480
  const narrow = viewportWidth <= 760
  const controlSize = compact ? 40 : DESKTOP_LAYOUT.controlSize
  const controlEdge = compact ? 4 : DESKTOP_LAYOUT.controlEdge
  const controlGap = compact ? 6 : 12
  const barPadding = compact ? 4 : narrow ? 8 : 12
  const iconSize = narrow ? 40 : 48
  const itemGap = narrow ? 4 : 8
  // 头像两侧等宽，每侧至少放下两个按钮。右翼的第一个按钮留给聊天。
  const minimumWidth = DESKTOP_LAYOUT.avatarSize + 4 * barPadding + 2 * (2 * iconSize + itemGap)
  const width = Math.max(0, viewportWidth - 2 * (controlEdge + controlSize + controlGap))

  return {
    width,
    barPadding,
    controlRight: controlEdge,
    controlSize,
    iconSize,
    itemGap,
    fits: viewportWidth >= DESKTOP_LAYOUT.minRailDisplayWidth && width >= minimumWidth
  }
}

export const DESKTOP_LAYOUT = {
  bottomInset: 96,
  contentInset: 12,
  barHeight: 64,
  barBottom: 12,
  avatarSize: 76,
  centerWidth: 100,
  controlSize: 44,
  controlEdge: 16,
  wingScrollSize: 20,
  wingScrollGap: 4
} as const

/** 与桌面底栏的响应式尺寸保持一致；每翼至少容纳一个可操作图标。 */
export function getDesktopRailLayout(viewportWidth: number) {
  const compact = viewportWidth <= 480
  const narrow = viewportWidth <= 760
  const controlSize = compact ? 40 : DESKTOP_LAYOUT.controlSize
  const controlEdge = compact ? 4 : DESKTOP_LAYOUT.controlEdge
  const controlGap = compact ? 6 : 12
  const centerWidth = compact ? DESKTOP_LAYOUT.avatarSize + 8 : DESKTOP_LAYOUT.centerWidth
  const barPadding = compact ? 4 : narrow ? 8 : 12
  const iconSize = narrow ? 40 : 48
  const itemGap = narrow ? 4 : 8

  const minimumWingWidth = Math.max(
    iconSize + 2 * itemGap + 8,
    iconSize + 2 * (DESKTOP_LAYOUT.wingScrollSize + DESKTOP_LAYOUT.wingScrollGap)
  )

  const minimumWidth = centerWidth + 2 * (barPadding + minimumWingWidth)
  const width = Math.max(0, viewportWidth - 2 * (controlEdge + controlSize + controlGap))

  return { width, controlRight: controlEdge, fits: width >= minimumWidth }
}

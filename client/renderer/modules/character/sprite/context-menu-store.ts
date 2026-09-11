import { atom, computed } from 'nanostores'

interface ContextMenuPos {
  x: number
  y: number
}

// 持久化位置以便菜单一直挂着、只切换可见性。
// 用 nanostore（而非 useState）让打开/关闭不会触发沉重的 CompanionRoot
// （8 个 useStore + 7 个 useState）重新渲染。
export const $contextMenuPos = atom<ContextMenuPos | null>(null)

// 派生 atom——供伙伴在菜单打开时跳过画布工作使用
// （如 `companion-3d.tsx` 用它门控 `pointermove` 注视跟踪）。
export const $contextMenuOpen = computed($contextMenuPos, pos => pos !== null)

export function openContextMenu(pos: ContextMenuPos): void {
  $contextMenuPos.set(pos)
}

export function closeContextMenu(): void {
  $contextMenuPos.set(null)
}

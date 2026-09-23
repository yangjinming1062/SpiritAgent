import { atom } from 'nanostores'

interface ContextMenuPos {
  x: number
  y: number
}

// 持久化位置以便菜单一直挂着、只切换可见性。
// 用 nanostore（而非 useState）让打开/关闭不会触发沉重的 CompanionRoot
// （8 个 useStore + 7 个 useState）重新渲染。
export const $contextMenuPos = atom<ContextMenuPos | null>(null)

export function openContextMenu(pos: ContextMenuPos): void {
  $contextMenuPos.set(pos)
}

export function closeContextMenu(): void {
  $contextMenuPos.set(null)
}

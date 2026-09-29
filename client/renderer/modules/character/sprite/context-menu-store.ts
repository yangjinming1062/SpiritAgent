import { atom } from 'nanostores'

interface ContextMenuPos {
  x: number
  y: number
}

// 菜单打开位置，null 表示关闭；菜单组件常驻挂载，只按它切换可见性。
// 触发方（精灵舞台、蛋形）与菜单经 atom 共享，开关只重渲染订阅的菜单组件。
export const $contextMenuPos = atom<ContextMenuPos | null>(null)

export function openContextMenu(pos: ContextMenuPos): void {
  $contextMenuPos.set(pos)
}

export function closeContextMenu(): void {
  $contextMenuPos.set(null)
}

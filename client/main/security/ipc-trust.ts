import type { BrowserWindow, WebContents } from 'electron'

/** sender 是否就是目标窗口的 webContents（窗口角色校验的最小公共原语）。 */
export function isSenderWindow(
  sender: Pick<WebContents, 'id'> | null | undefined,
  win: BrowserWindow | null | undefined
): boolean {
  return Boolean(
    sender && win && !win.isDestroyed() && !win.webContents.isDestroyed() && win.webContents.id === sender.id
  )
}

/** 活动探测等 UI 直调仅交给精灵宿主；模型调用由主进程网关派发。 */
export function assertSpriteHost(
  sender: Pick<WebContents, 'id'> | null | undefined,
  hostWindow: BrowserWindow | null | undefined,
  channel: string
): void {
  if (!isSenderWindow(sender, hostWindow)) {
    throw new Error(`${channel} is restricted to the sprite host window`)
  }
}

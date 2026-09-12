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

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

/** 本机工具与网关票据只交给持有网关的精灵宿主窗口（Client「连接与设备就绪」）；其他 sender 一律拒绝。 */
export function assertGatewayHost(
  sender: Pick<WebContents, 'id'> | null | undefined,
  hostWindow: BrowserWindow | null | undefined,
  channel: string
): void {
  if (!isSenderWindow(sender, hostWindow)) {
    throw new Error(`${channel} is restricted to the gateway host window`)
  }
}

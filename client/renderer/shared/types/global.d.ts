import type { DesktopBackgroundBridge, DesktopBridge } from '@client-shared/ipc/bridge'

export {}

declare global {
  interface Window {
    desktopBackground?: DesktopBackgroundBridge
    spiritagent: DesktopBridge
  }
}

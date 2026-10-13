import type { DesktopBackgroundBridge, DesktopBridge, DesktopFileBridge } from '@client-shared/ipc/bridge'

export {}

declare global {
  interface Window {
    desktopBackground?: DesktopBackgroundBridge
    spiritagent: DesktopBridge
    spiritagentWebUtils?: DesktopFileBridge
  }
}

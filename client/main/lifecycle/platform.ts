import type { App } from 'electron'

const GPU_OVERRIDE_ON: Set<string> = new Set(['1', 'true', 'yes', 'on'])
const GPU_OVERRIDE_OFF: Set<string> = new Set(['0', 'false', 'no', 'off'])

/** 判断是否在远程/转发显示器上运行（Chromium GPU 合成器会闪烁）；需要禁用 GPU 时返回 reason 字符串，否则 null。`SPIRITAGENT_DESKTOP_DISABLE_GPU` 可覆盖检测结果。 */
function detectRemoteDisplay(): null | string {
  const env = process.env

  const override = String(env.SPIRITAGENT_DESKTOP_DISABLE_GPU || '')
    .trim()
    .toLowerCase()

  if (GPU_OVERRIDE_ON.has(override)) {
    return 'override (SPIRITAGENT_DESKTOP_DISABLE_GPU)'
  }

  if (GPU_OVERRIDE_OFF.has(override)) {
    return null
  }

  if (env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) {
    return 'ssh-session'
  }

  if (process.platform === 'win32') {
    // RDP 会话上报的 SESSIONNAME 类似 "RDP-Tcp#7"；本地会话则是 "Console"。
    const sessionName = String(env.SESSIONNAME || '')

    if (/^rdp-/i.test(sessionName)) {
      return `rdp (SESSIONNAME=${sessionName})`
    }
  }

  return null
}

/** 须在 app ready 前调用：远程显示时关闭 GPU 加速并全局关闭 Chromium 后台节流（渲染功耗由引擎管理）。日志器尚未建立，检测结果直接写控制台；返回非 null 时精灵窗也须关闭透明。 */
export function applyChromiumSwitches(app: Pick<App, 'commandLine' | 'disableHardwareAcceleration'>): null | string {
  const remoteDisplayReason = detectRemoteDisplay()

  if (remoteDisplayReason) {
    app.disableHardwareAcceleration()
    app.commandLine.appendSwitch('disable-gpu-compositing')
    console.log(
      `[spiritagent] remote display detected (${remoteDisplayReason}); disabling GPU hardware acceleration to prevent flicker`
    )
  }

  app.commandLine.appendSwitch('disable-renderer-backgrounding')
  app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
  app.commandLine.appendSwitch('disable-background-timer-throttling')

  return remoteDisplayReason
}

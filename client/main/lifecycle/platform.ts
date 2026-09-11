const GPU_OVERRIDE_ON: Set<string> = new Set(['1', 'true', 'yes', 'on'])
const GPU_OVERRIDE_OFF: Set<string> = new Set(['0', 'false', 'no', 'off'])

interface DetectRemoteDisplayOptions {
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>
  platform?: string
}

/**
 * 判断应用是否在远程/转发的显示器上运行——这种场景下 Chromium 的 GPU 合成器
 * 会产生不稳定、闪烁的画面。
 * 需要禁用 GPU 时返回一个简短的 reason 字符串，否则返回 null。
 * `SPIRITAGENT_DESKTOP_DISABLE_GPU` 环境变量可以覆盖检测结果。
 * 纯函数、无依赖，便于单元测试。
 */
export function detectRemoteDisplay(options: DetectRemoteDisplayOptions = {}): null | string {
  const env = options.env ?? process.env
  const platform = options.platform ?? process.platform

  const override = String(env.SPIRITAGENT_DESKTOP_DISABLE_GPU || '')
    .trim()
    .toLowerCase()

  if (GPU_OVERRIDE_ON.has(override)) {
    return 'override (SPIRITAGENT_DESKTOP_DISABLE_GPU)'
  }

  if (GPU_OVERRIDE_OFF.has(override)) {
    return null
  }

  // SSH 会话 → 显示是 X11 转发或远程。
  if (env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) {
    return 'ssh-session'
  }

  if (platform === 'win32') {
    // RDP 会话上报的 SESSIONNAME 类似 "RDP-Tcp#7"；本地会话则是 "Console"。
    const sessionName = String(env.SESSIONNAME || '')

    if (/^rdp-/i.test(sessionName)) {
      return `rdp (SESSIONNAME=${sessionName})`
    }
  }

  return null
}

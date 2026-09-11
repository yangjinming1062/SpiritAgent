// 角色侧主动性台词出口：仪式行走失败兜底等需要按打扰档位说一句的场合经此送达；
// 实现（气泡 + TTS 交付）由 app/workflows/proactive-delivery 在启动时绑定，
// 角色模块不反向依赖应用层。
export interface ProactiveLineOptions {
  affect?: string
  sessionId?: string
  userInitiated?: boolean
}

type ProactiveLineSpeaker = (text: string, opts?: ProactiveLineOptions) => Promise<void>

let speaker: ProactiveLineSpeaker | null = null

export function bindProactiveLineSpeaker(next: ProactiveLineSpeaker): void {
  speaker = next
}

export function speakProactiveLine(text: string, opts?: ProactiveLineOptions): Promise<void> {
  if (!speaker) {
    throw new Error('proactive line speaker not bound — app bootstrap must initialize first')
  }

  return speaker(text, opts)
}

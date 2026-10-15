import type React from 'react'
import { useState } from 'react'
import { Button } from '../components/button'
import { launchSpiritAgentDesktop } from '../store'
import { Rocket, AlertCircle } from 'lucide-react'

// 启动失败就地展示 Tauri 错误
export default function Success(): React.JSX.Element {
  const [error, setError] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)

  async function handleLaunch() {
    setError(null)
    setLaunching(true)
    try {
      await launchSpiritAgentDesktop()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setLaunching(false)
    }
  }

  return (
    <div className="spiritagent-fade-in installer-screen relative isolate flex h-full flex-col items-center gap-8 px-12 py-10">
      <span aria-hidden="true" className="spiritagent-glow" />

      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="mx-auto mb-5 text-[clamp(2rem,5vw,3.5rem)] font-bold leading-tight tracking-tight text-accent">唤生已准备就绪</h1>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-text-body">
          打开唤生，认识你的伙伴。以后也可以从电脑的应用列表中找到唤生。
        </p>
      </div>

      <Button
        onClick={() => void handleLaunch()}
        size="lg"
        disabled={launching}
        className="inline-flex items-center gap-2 px-6"
      >
        <Rocket size={18} />
        {launching ? '启动中…' : '启动 唤生'}
      </Button>

      {error && (
        <div
          role="alert"
          className="flex max-w-2xl items-start gap-2 rounded-md border border-destructive/40 bg-destructive/15 px-4 py-3 text-sm text-destructive backdrop-blur-xs"
        >
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <div className="min-w-0">
            <div className="font-medium">无法启动桌面应用</div>
            <p className="mt-1 text-destructive/85">请从电脑的应用列表打开唤生，或再次点击上方按钮。</p>
            <details className="mt-2 text-xs"><summary>查看问题详情</summary>{error}</details>
          </div>
        </div>
      )}
    </div>
  )
}

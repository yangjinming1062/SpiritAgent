import type React from 'react'
import { useState } from 'react'
import { type CSSProperties } from 'react'
import { Button } from '../components/button'
import { launchSpiritAgentDesktop } from '../store'
import { Rocket, AlertCircle } from 'lucide-react'

// 成功页与欢迎页共享视觉锚点；启动失败时把 Tauri 错误就地展示，不静默吞掉。
export default function Success(): React.JSX.Element {
  const [error, setError] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)

  async function handleLaunch() {
    setError(null)
    setLaunching(true)
    try {
      await launchSpiritAgentDesktop()
      // 启动成功时安装器随之退出，流程不会回到这里。
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setLaunching(false)
    }
  }

  return (
    <div className="spiritagent-fade-in relative isolate flex h-full flex-col items-center justify-center gap-8 px-12 py-10">
      <span aria-hidden="true" className="spiritagent-glow" />

      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-accent"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '5rem',
              '--fit-text-min': '2.25rem'
            } as CSSProperties
          }
        >
          <span>
            <span>唤生 已准备就绪</span>
          </span>
          <span aria-hidden="true">唤生 已准备就绪</span>
        </p>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-text-body">
          点击下方按钮启动 唤生 桌面应用。安装后您也可以随时从开始菜单或系统托盘找到它。
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
            <div className="mt-1 text-destructive/85">{error}</div>
          </div>
        </div>
      )}
    </div>
  )
}

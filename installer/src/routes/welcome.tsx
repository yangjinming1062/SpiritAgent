import type React from 'react'
import { Button } from '../components/button'
import { startInstall } from '../store'
import { ArrowRight } from 'lucide-react'

export default function Welcome(): React.JSX.Element {
  return (
    <div className="spiritagent-fade-in installer-screen relative isolate flex h-full flex-col items-center gap-10 px-12 py-10">
      <span aria-hidden="true" className="spiritagent-glow" />

      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="mx-auto mb-5 text-[clamp(2rem,5vw,3.5rem)] font-bold leading-tight tracking-tight text-accent">欢迎使用唤生</h1>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-text-body">
          安装完成后，就可以认识你的 AI 伙伴。应用和常用功能会自动准备好。
        </p>
        <p className="mt-4 text-sm leading-relaxed text-text-muted">
          请保持网络连接，无需选择或配置技术选项。安装过程中可能需要你允许系统操作。
        </p>
      </div>

      <Button
        onClick={() => void startInstall()}
        size="lg"
        className="group inline-flex items-center gap-2 px-6"
      >
        开始安装
        <ArrowRight
          size={18}
          className="transition-transform group-hover:translate-x-0.5"
        />
      </Button>
    </div>
  )
}

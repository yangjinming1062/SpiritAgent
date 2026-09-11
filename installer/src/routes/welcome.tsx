import type React from 'react'
import { type CSSProperties } from 'react'
import { Button } from '../components/button'
import { startInstall } from '../store'
import { ArrowRight } from 'lucide-react'

export default function Welcome(): React.JSX.Element {
  return (
    <div className="spiritagent-fade-in relative isolate flex h-full flex-col items-center justify-center gap-10 px-12 py-10">
      <span aria-hidden="true" className="spiritagent-glow" />

      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-accent"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '6rem',
              '--fit-text-min': '2.5rem'
            } as CSSProperties
          }
        >
          <span>
            <span>欢迎使用 唤生</span>
          </span>
          <span aria-hidden="true">欢迎使用 唤生</span>
        </p>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-text-body">
          您的智能助手。我们将在后台完成设置，请稍候几分钟。
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

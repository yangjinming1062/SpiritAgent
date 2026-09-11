import type React from 'react'
import { type CSSProperties } from 'react'
import { useStore } from '@nanostores/react'
import { Button } from '../components/button'
import {
  $logPath,
  openLogDir,
  startInstall,
  type BootstrapStateModel
} from '../store'
import { RefreshCw, FileText } from 'lucide-react'

interface FailureProps {
  bootstrap: BootstrapStateModel
}

// 失败页：保持品牌主视觉，错误文案置于次级文字，主操作是重试。
export default function Failure({ bootstrap }: FailureProps): React.JSX.Element {
  const logPath = useStore($logPath)

  return (
    <div className="spiritagent-fade-in flex h-full flex-col items-center justify-center gap-6 px-12 py-10">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-destructive"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '5rem',
              '--fit-text-min': '2.25rem'
            } as CSSProperties
          }
        >
          <span>
            <span>安装未完成</span>
          </span>
          <span aria-hidden="true">安装未完成</span>
        </p>

        <p className="m-0 mx-auto max-w-xl text-center text-sm leading-normal tracking-tight text-text-body">
          {bootstrap.error ?? '安装过程中出现了问题。'}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={() => void startInstall()}
          size="lg"
          className="inline-flex items-center gap-2 px-6"
        >
          <RefreshCw size={16} />
          重新安装
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={() => void openLogDir()}
          className="inline-flex items-center gap-2"
        >
          <FileText size={16} />
          打开日志文件夹
        </Button>
      </div>

      {logPath && (
        <p className="max-w-lg text-center text-xs text-text-muted">
          日志： <code className="font-mono text-text-body">{logPath}</code>
        </p>
      )}
    </div>
  )
}

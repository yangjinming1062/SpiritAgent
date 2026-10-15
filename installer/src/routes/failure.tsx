import type React from 'react'
import { useState } from 'react'
import { useStore } from '@nanostores/react'
import { Button } from '../components/button'
import {
  $logPath,
  openLogDir,
  stageLabel,
  startInstall,
  type BootstrapStateModel
} from '../store'
import { RefreshCw, FileText } from 'lucide-react'

interface FailureProps {
  bootstrap: BootstrapStateModel
}

export default function Failure({ bootstrap }: FailureProps): React.JSX.Element {
  const logPath = useStore($logPath)
  const [logError, setLogError] = useState('')
  const failed = bootstrap.stageOrder.find(name => bootstrap.stages[name]?.state === 'failed')
  const raw = bootstrap.error ?? ''
  const recovery = /cancel|取消|终止/i.test(raw)
    ? '安装已停止。想继续时，点击下方按钮重新开始。'
    : /network|download|connect|timeout|网络|下载|连接|超时/i.test(raw)
      ? '有些安装文件没有下载完成。请检查网络连接，再试一次。'
      : /disk|space|磁盘|空间/i.test(raw)
        ? '可用空间不足。请清理一些磁盘空间，再试一次。'
        : /permission|access.denied|权限|拒绝访问/i.test(raw)
          ? '系统没有允许完成安装。重试时，请允许安装器的系统提示。'
          : '安装遇到了问题。请关闭正在运行的唤生，再重试安装。'

  return (
    <div className="spiritagent-fade-in installer-screen flex h-full flex-col items-center gap-6 px-12 py-10">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="mx-auto mb-5 text-[clamp(2rem,5vw,3.5rem)] font-bold leading-tight tracking-tight text-destructive">安装未完成</h1>

        <p className="m-0 mx-auto max-w-xl text-center text-sm leading-normal tracking-tight text-text-body">
          {recovery}
        </p>
        {failed ? <p className="mt-3 text-sm text-text-muted">停在“{stageLabel(failed)}”这一步。</p> : null}
        <p className="mt-3 text-sm text-text-muted">已有资料会保留，重试会重新准备应用文件。</p>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={() => void startInstall()}
          size="lg"
          className="inline-flex items-center gap-2 px-6"
        >
          <RefreshCw size={16} />
          重试安装
        </Button>
      </div>

      <details className="w-full max-w-xl overflow-auto rounded-lg border border-line-hairline px-4 py-3 text-sm text-text-muted">
        <summary className="cursor-pointer">查看问题详情</summary>
        <p className="my-3 max-h-28 overflow-auto break-words whitespace-pre-wrap font-mono text-xs">{raw || '没有更多问题详情。'}</p>
        <Button variant="outline" size="sm" onClick={() => {
          setLogError('')
          void openLogDir().catch((error: unknown) => setLogError(error instanceof Error ? error.message : String(error)))
        }} className="inline-flex items-center gap-2">
          <FileText size={16} />打开日志文件夹
        </Button>
        {logPath ? <p className="mt-3 break-all text-xs">日志位置：{logPath}</p> : null}
        {logError ? <p className="mt-3 text-xs text-destructive" role="alert">无法打开日志文件夹，请按上方位置查找。{logError}</p> : null}
      </details>
    </div>
  )
}

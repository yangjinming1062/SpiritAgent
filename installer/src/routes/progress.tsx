import type React from 'react'
import { useState } from 'react'
import { useStore } from '@nanostores/react'
import { Button } from '../components/button'
import { Egg, type EggPhase } from '../components/egg'
import { Halo } from '../components/halo'
import { StageList } from '../components/stage-list'
import {
  cancelInstall,
  $progress,
  stageLabel,
  type BootstrapStateModel
} from '../store'
import { FileText, ChevronRight, Loader2 } from 'lucide-react'
import clsx from 'clsx'

interface ProgressProps {
  bootstrap: BootstrapStateModel
}

export default function ProgressScreen({ bootstrap }: ProgressProps): React.JSX.Element {
  const progress = useStore($progress)
  const [showDetails, setShowDetails] = useState(false)
  const [actionError, setActionError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const visualSize = showDetails ? 112 : 260

  const stageOrder = bootstrap.stageOrder
  const currentStageName = bootstrap.currentStage
  const currentStageIdx = currentStageName ? stageOrder.indexOf(currentStageName) : -1

  let phase: EggPhase = 'idle'
  if (bootstrap.status === 'completed') {
    phase = 'hatching'
  } else if (bootstrap.status === 'failed') {
    phase = 'failed'
  } else if (progress.done > 0) {
    phase = 'cracking'
  }

  const failedIdx = stageOrder.findIndex((name) => bootstrap.stages[name]?.state === 'failed')
  const failedAt = failedIdx >= 0 ? failedIdx : null

  const captionText =
    bootstrap.status === 'completed'
      ? '安装完成，马上就好'
      : bootstrap.status === 'failed'
        ? '安装暂时停止，正在准备恢复选项'
        : currentStageName
          ? stageLabel(currentStageName)
          : '准备中…'

  return (
    <div className="spiritagent-fade-in relative isolate flex h-full flex-col overflow-hidden">
      {/* 背景氛围光 */}
      <span aria-hidden="true" className="spiritagent-glow" />

      {/* 进度细条：与顶部 Titlebar 区分，仅显示步骤进度计数；状态文案由下方 captionText 承担 */}
      <div className="flex shrink-0 items-center justify-end border-b border-line-hairline px-6 py-1.5 text-[11px] font-medium text-text-muted backdrop-blur-xs">
        {progress.total ? `已完成 ${progress.done} / ${progress.total} 个步骤` : '正在准备安装'}
      </div>

      {/* 区域 B：主视觉蛋与光环 */}
      <div className="relative flex flex-1 flex-col items-center justify-center min-h-0 px-6 py-2">
        {/* 展开详情时缩小主视觉，为最小窗口保留操作空间。 */}
        <div className="relative flex shrink-0 items-center justify-center" style={{ width: visualSize, height: visualSize }}>
          {/* 外层分段光环 */}
          <Halo
            total={progress.total || 6}
            done={progress.done}
            runningIdx={bootstrap.status === 'running' && currentStageIdx >= 0 ? currentStageIdx : null}
            failedAt={failedAt}
            size={visualSize}
            className="absolute inset-0"
          />

          {/* 中央蛋 */}
          <Egg
            cracks={progress.done}
            phase={phase}
            size={showDetails ? 88 : 208}
          />

          {/* 破壳覆盖层，居中叠在主视觉上 */}
          {bootstrap.status === 'completed' && (
            <div
              className="pointer-events-none absolute -inset-8 rounded-full bg-radial from-amber-200/35 via-amber-100/10 to-transparent opacity-0 animate-warm-glow"
              aria-hidden="true"
            />
          )}
        </div>

        {/* 主视觉下方的动态文案 */}
        <div aria-live="polite" className="mt-4 flex items-center gap-2 text-center text-base font-medium text-text-body" role="status">
          {bootstrap.status === 'running' && (
            <Loader2 size={14} className="animate-spin text-accent shrink-0" />
          )}
          <span className="tracking-tight">{captionText}</span>
        </div>
        <p className="mt-3 text-center text-sm leading-relaxed text-text-muted">
          {bootstrap.status === 'running' || bootstrap.status === 'idle' ? '请保持网络连接，安装会自动继续。' : '已有资料会保留在本机。'}
        </p>
        {actionError ? <div className="mt-3 max-w-md text-center text-sm text-destructive" role="alert">无法停止安装，请稍后再试。<details><summary>查看问题详情</summary>{actionError}</details></div> : null}
      </div>

      {/* 折叠展开的详情面板 */}
      {showDetails && (
        <div className="mx-8 mb-2 h-44 shrink-0 overflow-hidden">
          <StageList bootstrap={bootstrap} />
        </div>
      )}

      {/* 区域 C：底部工具栏 */}
      <div className="flex shrink-0 items-center justify-between border-t border-line-hairline px-8 py-3 backdrop-blur-xs">
        <button
          type="button"
          aria-expanded={showDetails}
          onClick={() => setShowDetails((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-text-muted transition-colors hover:text-text-strong"
        >
          <FileText size={14} />
          {showDetails ? '收起安装详情' : '查看安装详情'}
          <ChevronRight
            size={12}
            className={clsx('transition-transform duration-200', showDetails && 'rotate-90')}
          />
        </button>

        {bootstrap.status === 'running' && (
          <Button variant="outline" size="sm" disabled={cancelling} onClick={() => {
            setCancelling(true)
            setActionError('')
            void cancelInstall().catch((error: unknown) => {
              setActionError(error instanceof Error ? error.message : String(error))
              setCancelling(false)
            })
          }}>
            {cancelling ? '正在停止…' : '停止安装'}
          </Button>
        )}
      </div>
    </div>
  )
}

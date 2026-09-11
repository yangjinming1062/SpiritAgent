import type React from 'react'
import { useState } from 'react'
import { useStore } from '@nanostores/react'
import { Button } from '../components/button'
import { Egg, type EggPhase } from '../components/egg'
import { Halo } from '../components/halo'
import { HatchOverlay } from '../components/hatch-overlay'
import { StageList } from '../components/stage-list'
import {
  cancelInstall,
  $progress,
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

  const stageOrder = bootstrap.stageOrder
  const currentStageName = bootstrap.currentStage
  const currentStageIdx = currentStageName ? stageOrder.indexOf(currentStageName) : -1

  const currentStageObj = currentStageName ? bootstrap.stages[currentStageName] : null

  // 根据状态决定蛋的阶段
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
      ? '破壳而生，准备就绪！'
      : bootstrap.status === 'failed'
        ? '安装中断，遇到了麻烦'
        : currentStageObj
          ? currentStageObj.info.title
          : '准备中…'

  return (
    <div className="spiritagent-fade-in relative isolate flex h-full flex-col overflow-hidden">
      {/* 背景氛围光 */}
      <span aria-hidden="true" className="spiritagent-glow" />

      {/* 进度细条：与顶部 Titlebar 区分，仅显示步骤进度计数；状态文案由下方 captionText 承担 */}
      <div className="flex shrink-0 items-center justify-end border-b border-line-hairline px-6 py-1.5 text-[11px] font-medium text-text-muted backdrop-blur-xs">
        第 {progress.done} 步 · 共 {progress.total} 步
      </div>

      {/* 区域 B：主视觉蛋与光环 */}
      <div className="relative flex flex-1 flex-col items-center justify-center min-h-0 px-6 py-2">
        {/* 父容器锁定为光环尺寸（300），让 Halo 用 absolute inset-0 全铺，Egg 居中——否则 Halo absolute 后脱离 flex 流，flex 容器按 Egg 240 收缩，Halo 左上对齐导致光环偏到蛋的左侧。 */}
        <div className="relative flex h-[300px] w-[300px] items-center justify-center">
          {/* 外层分段光环 */}
          <Halo
            total={progress.total || 6}
            done={progress.done}
            runningIdx={bootstrap.status === 'running' && currentStageIdx >= 0 ? currentStageIdx : null}
            failedAt={failedAt}
            size={300}
            className="absolute inset-0"
          />

          {/* 中央蛋 */}
          <Egg
            cracks={progress.done}
            phase={phase}
            size={240}
          />

          {/* 破壳覆盖层，居中叠在主视觉上 */}
          <HatchOverlay active={bootstrap.status === 'completed'} />
        </div>

        {/* 主视觉下方的动态文案 */}
        <div className="mt-4 flex items-center gap-2 text-center text-sm font-medium text-text-body">
          {bootstrap.status === 'running' && (
            <Loader2 size={14} className="animate-spin text-accent shrink-0" />
          )}
          <span className="tracking-tight">{captionText}</span>
        </div>
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
          onClick={() => setShowDetails((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-text-muted transition-colors hover:text-text-strong"
        >
          <FileText size={14} />
          {showDetails ? '隐藏详情' : '显示详情'}
          <ChevronRight
            size={12}
            className={clsx('transition-transform duration-200', showDetails && 'rotate-90')}
          />
        </button>

        {bootstrap.status === 'running' && (
          <Button variant="outline" size="sm" onClick={() => void cancelInstall()}>
            取消安装
          </Button>
        )}
      </div>
    </div>
  )
}

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { $defaultScale, hydrateAvatarSeeds, hydrateWardrobe, setDefaultScale } from '@/modules/character'
import {
  $mesh2dInfo,
  $renderMode,
  type RenderMode,
  requestMesh2DGeneration,
  Seed3dWizard,
  switchRenderMode
} from '@/modules/character/rendering/2d'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE, Segmented, Slider } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import { Model3dSection } from './model3d-section'
import { OutfitSection } from './outfit-section'

interface Seed3dWizardState {
  avatarId: number
  supportsMultiview: boolean
}

// 外观页（DESIGN §5.5 / §6.1）：顶栏左侧切换渲染模式（2D / 3D，即桌面实际显示形态），
// 右侧形象大小为跨模式共享的桌面显示设置。2D 模式提供着装设计与切换（OutfitSection），
// 3D 模式提供建模状态与形象更新（Model3dSection）。外观列表与种子图在页级水合，两种模式共用。
export function AppearancePage(): React.JSX.Element {
  const renderMode = useStore($renderMode)
  const mesh2dInfo = useStore($mesh2dInfo)
  const defaultScale = useStore($defaultScale)
  const authKind = useStore($auth).kind
  const t = useStrings().living.appearance
  const [seed3dWizard, setSeed3dWizard] = useState<Seed3dWizardState | null>(null)

  // 外观列表在 auth 就绪后再水合——冷启动直接进入本页时 hydrateAuth 的 IPC 往返
  // 尚未完成，提前调用会因 pending 静默跳过。种子图走本地缓存，缺失时补拉。
  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateWardrobe()
      void hydrateAvatarSeeds()
    }
  }, [authKind])

  // 切 3D 前先补 3D 种子图：2D 正面种子的自然站姿不满足 3D 建模（A-pose），
  // 且只在这一刻才值得付生图（onboarding 只确认 2D 正面）。3D 正面缺或（多视角时）背面缺则出向导；
  // 非多视角供应商已有 3D 正面时直接切换。
  const onRenderModeClick = async (m: RenderMode): Promise<void> => {
    if (m === '3d' && renderMode !== '3d') {
      try {
        const res = await window.spiritagent.api<{
          id?: number
          seed_front_3d_url?: string | null
          seed_back_url?: string | null
          supports_multiview?: boolean
        }>({ path: '/api/companion/avatar' })

        if (res.id != null && (!res?.seed_front_3d_url || (res?.supports_multiview && !res?.seed_back_url))) {
          setSeed3dWizard({ avatarId: res.id, supportsMultiview: res.supports_multiview === true })

          return
        }
      } catch {
        // 头像行拉取失败时按直接切换处理；缺 3D 种子输入会在 3D 派发处以后端报错暴露，用户可重试。
      }
    }

    void switchRenderMode(m)
  }

  const mesh2dRetryable = mesh2dInfo.status !== 'succeeded' && mesh2dInfo.status !== 'generating'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 顶栏：渲染模式切换 + 形象大小（跨模式共享） */}
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line-hairline px-4 py-2.5">
        <Segmented<RenderMode>
          onChange={m => void onRenderModeClick(m)}
          options={[
            { value: '2d', label: t.mode2d },
            { value: '3d', label: t.mode3d }
          ]}
          value={renderMode}
        />
        <div className="flex items-center gap-2.5" title={t.companionSizeHint}>
          <span className="text-[11px] font-medium text-strong">{t.companionSize}</span>
          <div className="w-28">
            <Slider
              ariaLabel={t.scaleAria}
              max={3}
              min={0.3}
              onChange={setDefaultScale}
              step={0.05}
              value={defaultScale}
            />
          </div>
          <span className="w-9 shrink-0 text-right text-xs tabular-nums text-body">
            {String(Number(defaultScale.toFixed(2)))}×
          </span>
        </div>
      </div>

      {renderMode === '2d' ? (
        <>
          {/* DESIGN §5.5：2D 切分失败（或尚无 2D 资产）时提供重试入口 */}
          {mesh2dRetryable && (
            <div className="mx-4 mt-3 flex items-center justify-between rounded-xl border border-line-hairline bg-surface-card px-3.5 py-2.5">
              <span className="text-xs text-body">
                {mesh2dInfo.status === 'failed' ? t.mesh2dFailed : t.mesh2dMissing}
              </span>
              <button
                className={cn(BTN_SUBTLE, 'h-7 px-3')}
                onClick={() => void requestMesh2DGeneration()}
                type="button"
              >
                {t.mesh2dRetry}
              </button>
            </div>
          )}
          <OutfitSection />
        </>
      ) : (
        <Model3dSection />
      )}

      {seed3dWizard != null && (
        <Seed3dWizard
          avatarId={seed3dWizard.avatarId}
          onCancel={() => setSeed3dWizard(null)}
          onConfirm={() => {
            setSeed3dWizard(null)
            void switchRenderMode('3d')
          }}
          supportsMultiview={seed3dWizard.supportsMultiview}
        />
      )}
    </div>
  )
}

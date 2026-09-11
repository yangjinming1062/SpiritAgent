import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import {
  $mesh2dInfo,
  $renderMode,
  type RenderMode,
  requestMesh2DGeneration,
  Seed3dWizard,
  switchRenderMode
} from '@/2d'
import { $defaultScale, setDefaultScale } from '@/companion'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE, HINT_TEXT, Segmented, SettingsContent, Slider } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface Seed3dWizardState {
  avatarId: number
  supportsMultiview: boolean
}

// 形象页：渲染模式（2D / 3D）、2D 动画资产状态与重试、桌面显示比例。
export function AppearancePage(): React.ReactElement {
  const renderMode = useStore($renderMode)
  const mesh2dInfo = useStore($mesh2dInfo)
  const defaultScale = useStore($defaultScale)
  const t = useStrings().living.appearance
  const [seed3dWizard, setSeed3dWizard] = useState<Seed3dWizardState | null>(null)

  // 切 3D 前先补 3D 种子图：2D 正面种子的站姿与画风都不满足 3D 建模（A-pose、3D 画风），
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
    <>
      <SettingsContent>
        <section>
          <h3 className="text-xs font-medium text-strong">{t.renderMode}</h3>
          <p className={cn(HINT_TEXT, 'mt-1')}>{t.renderModeHint}</p>
          <div className="mt-2.5">
            <Segmented<RenderMode>
              onChange={m => void onRenderModeClick(m)}
              options={[
                { value: '2d', label: t.mode2d },
                { value: '3d', label: t.mode3d }
              ]}
              value={renderMode}
            />
          </div>

          {/* DESIGN §5.5：2D 切分失败（或尚无 2D 资产）时提供重试入口 */}
          {renderMode === '2d' && mesh2dRetryable && (
            <div className="mt-2.5 flex items-center justify-between rounded-xl border border-line-hairline bg-surface-card px-3.5 py-2.5">
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
        </section>

        <section className="mt-6">
          <h3 className="text-xs font-medium text-strong">{t.companionSize}</h3>
          <p className={cn(HINT_TEXT, 'mt-1')}>{t.companionSizeHint}</p>
          <div className="mt-3 flex max-w-sm items-center gap-3">
            <Slider
              ariaLabel={t.scaleAria}
              max={3}
              min={0.3}
              onChange={setDefaultScale}
              step={0.05}
              value={defaultScale}
            />
            <span className="w-11 shrink-0 text-right text-xs tabular-nums text-body">
              {String(Number(defaultScale.toFixed(2)))}×
            </span>
          </div>
          <p className={cn(HINT_TEXT, 'mt-1.5')}>{t.scaleRange}</p>
        </section>
      </SettingsContent>

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
    </>
  )
}

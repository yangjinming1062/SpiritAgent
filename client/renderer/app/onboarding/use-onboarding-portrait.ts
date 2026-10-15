import { useStore } from '@nanostores/react'
import { useCallback, useMemo, useRef, useState } from 'react'

import {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  clearPortraitHistory,
  hydratePortraitHistory,
  loadDraftRefImage,
  patchAvatarSeeds,
  pickAvatarImage,
  type PickedImage,
  type PortraitEntry,
  pushPortraitEntry,
  saveDraftRefImage,
  selectAvatar,
  selectPortraitEntry
} from '@/modules/character'
import type { HistoryGalleryItem } from '@/shared'
import { authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { backendApi } from '@/shared/platform/desktop'

import type { Phase } from './back-transition'
import { useRegeneratePortrait } from './use-regenerate-portrait'

interface AvatarResponse {
  asset_url?: string | null
  id?: number
}

interface SavedAvatarResponse {
  asset_url: string
  id: number
}

interface PortraitOptions {
  begin: () => () => boolean
  imageSealed: boolean
  setHint: (hint: string | null) => void
  setPhase: (phase: Phase) => void
  enterVoiceSelection: () => void
}

export type OnboardingPortrait = ReturnType<typeof useOnboardingPortrait>

// 头像预览、身份参考草稿与确认操作归此视图持有，形象资产的正式状态仍归 character。
export function useOnboardingPortrait({ begin, imageSealed, setHint, setPhase, enterVoiceSelection }: PortraitOptions) {
  const activeAvatarId = useStore($activeAvatarId)
  const portraitHistory = useStore($portraitHistory)
  const portraitSelectedIdx = useStore($portraitSelectedIdx)
  const [portraitDirectAdopt, setPortraitDirectAdopt] = useState(false)
  const [sealingPortrait, setSealingPortrait] = useState(false)
  const sealingRef = useRef(false)
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [portraitPreviewId, setPortraitPreviewId] = useState<number | null>(null)
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)
  const [avatarSelfSourceOpen, setAvatarSelfSourceOpen] = useState(false)
  const [refImage, setRefImage] = useState<PickedImage | null>(null)
  // 构图参考仅用于本次重绘，不进入持久草稿。
  const [presentationRef, setPresentationRef] = useState<PickedImage | null>(null)

  // 失败时保留当前头像：它已持有解析好的字节。
  const applyLocalPortrait = async (
    response: AvatarResponse | null | undefined,
    isLive: () => boolean
  ): Promise<string | null> => {
    const { avatar } = await applyPortrait({ id: response?.id, assetUrl: response?.asset_url }, isLive)

    if (isLive() && avatar) {
      setPortraitUrl(avatar)
      setPortraitPreviewId(response?.id ?? null)
    }

    return avatar
  }

  const updateRefImage = (img: PickedImage | null): void => {
    setRefImage(img)

    if (!img) {
      setPresentationRef(null)
    }

    void saveDraftRefImage(img)
  }

  const startAiHatching = async (): Promise<void> => {
    if (imageSealed) {
      return
    }

    const isLive = begin()
    setPhase('hatching')
    setPortraitDirectAdopt(false)
    setPortraitPanelHint(null)
    setHint(null)

    const succeeded = await generateAvatarPortrait()

    if (!isLive() || succeeded === null) {
      return
    }

    // 失败时同样进入确认步骤，由头像面板显示原因。
    setPhase('portrait-avatar')
  }

  // 新建一行 avatar 并经 hook 发布到 $activeAvatarId；微调编辑上一版，重新生成保持种子全量重绘；附参考图时微调不可用。
  const {
    generate: generateAvatarPortrait,
    regenerate: regenerateAvatarPortrait,
    edit: editAvatarPortrait,
    reload: reloadAvatarPortrait,
    busy: avatarBusy
  } = useRegeneratePortrait({
    refImage,
    presentationRef,
    onRegenerated: ({ avatar, id }) => {
      setPortraitPanelHint(null)
      // 重绘与微调产物按 AI 结果对待，需经确认步骤。
      setPortraitDirectAdopt(false)

      if (avatar) {
        setPortraitUrl(avatar)
        setPortraitPreviewId(id)
      }
    },
    onError: setPortraitPanelHint
  })

  const currentHistoryItems: HistoryGalleryItem[] = useMemo(
    () => portraitHistory.map(e => ({ url: e.portraitUrl })),
    [portraitHistory]
  )

  const onSelectHistoryEntry = useCallback(
    (idx: number) => {
      const entry: PortraitEntry | undefined = portraitHistory[idx]

      if (!entry) {
        return
      }

      selectPortraitEntry(idx)

      if (entry.portraitUrl) {
        setPortraitUrl(entry.portraitUrl)
        setPortraitPreviewId(entry.avatarId)
        $portraitUrl.set(entry.portraitUrl)
      }

      // 点选画廊时须同步当前 avatar 行，否则选中会悄悄回退到最后一行，画面跳回已拒绝的脸。
      if (entry.avatarId != null) {
        $activeAvatarId.set(entry.avatarId)
        void selectAvatar(entry.avatarId)
      }
    },
    [portraitHistory]
  )

  const pickImage = async (title: string, onPicked: (image: PickedImage) => void): Promise<void> => {
    const isLive = begin()
    const picked = await pickAvatarImage(title)

    if (!isLive() || !picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    onPicked(picked.image)
    setHint(null)
  }

  const pickReferenceImage = (): Promise<void> => pickImage('选择一张参考图', updateRefImage)

  const pickPresentationImage = (): Promise<void> => pickImage('选择光线与构图参考图', setPresentationRef)

  const fetchAvatarPrompt = async (): Promise<string> => {
    const response = await backendApi().api<{ prompt: string }>({
      path: '/api/companion/avatar/prompt',
      method: 'POST',
      body: { feedback: $regenFeedback.get().trim() || undefined, has_reference: Boolean(refImage) }
    })

    return response.prompt
  }

  const adoptAvatarSeed = async (image: PickedImage): Promise<void> => {
    const isLive = begin()

    const response = await backendApi().api<SavedAvatarResponse>({
      path: '/api/companion/avatar/adopt',
      method: 'POST',
      body: { image: image.base64, content_type: image.contentType }
    })

    if (!isLive()) {
      return
    }

    const avatar = await applyLocalPortrait(response, isLive)

    if (!isLive()) {
      return
    }

    if (!avatar) {
      throw new Error('头像已保存，预览加载失败，请重新加载')
    }

    const avatarId = response.id ?? null

    pushPortraitEntry({ assetUrl: response.asset_url ?? null, avatarId, portraitUrl: avatar })
    $regenFeedback.set('')
    setPresentationRef(null)
    setPortraitPanelHint(null)
    // 自备图即心仪头像，采纳后直接确认。
    setPortraitDirectAdopt(true)
    await sealPortrait(avatarId)
  }

  // 确认头像并进入全身阶段；失败落到确认步骤展示原因，可原地重试。
  const sealPortrait = async (expectedAvatarId: number | null = portraitPreviewId): Promise<void> => {
    if (sealingRef.current || expectedAvatarId === null) {
      return
    }

    const isLive = begin()
    sealingRef.current = true
    setSealingPortrait(true)

    try {
      const result = await authedApi({
        path: '/api/companion/portrait/confirm',
        method: 'POST',
        body: { expected_avatar_id: expectedAvatarId }
      })

      if (!isLive() || (!result.ok && result.reason === 'unauth')) {
        return
      }

      if (!result.ok) {
        throw result.error
      }
    } catch (error) {
      // 确认失败（如 409 头像已更新或临时文件过期）绝不能推进：退回确认步骤说明原因；onClick 的 void 会吞异常，故这里显式提示。
      if (!isLive()) {
        return
      }

      log.warn('onboarding', 'portrait confirm failed', error)
      setPortraitPanelHint(backendDetailMessage(error, '确认失败，请检查网络后重试'))
      setPhase('portrait-avatar')

      return
    } finally {
      sealingRef.current = false

      if (isLive()) {
        setSealingPortrait(false)
      }
    }

    clearPortraitHistory()
    setPresentationRef(null)
    $regenFeedback.set('')

    setPhase('fullbody-reference')
  }

  // 草稿此前已预览过，继续即确认。
  const continueCurrentPortrait = async (): Promise<void> => {
    setPortraitDirectAdopt(true)
    await sealPortrait()
  }

  const confirmFullbody = async (expectedUrl: string): Promise<void> => {
    if (!activeAvatarId) {
      throw new Error('请先选择头像')
    }

    const isLive = begin()

    const res = await backendApi().api<SavedAvatarResponse & { seed_fullbody_url: string }>({
      path: `/api/companion/avatar/${activeAvatarId}/fullbody/confirm`,
      method: 'POST',
      body: { expected_url: expectedUrl }
    })

    if (!isLive() || $activeAvatarId.get() !== activeAvatarId) {
      return
    }

    await applyLocalPortrait(res, isLive)

    if (!isLive()) {
      return
    }

    await patchAvatarSeeds({ avatarId: res.id, fullbodySeedUrl: res.seed_fullbody_url })

    if (!isLive()) {
      return
    }

    enterVoiceSelection()
  }

  const restoreReference = async (isLive: () => boolean): Promise<void> => {
    const cached = await loadDraftRefImage()

    if (isLive() && cached) {
      setRefImage(cached)
    }
  }

  const restorePortrait = async (
    nextField: 'portrait' | 'fullbody-reference',
    isLive: () => boolean
  ): Promise<Phase> => {
    try {
      if (nextField === 'portrait') {
        await hydratePortraitHistory()
      }

      if (!isLive()) {
        return 'portrait-generate'
      }

      const response = await backendApi().api<AvatarResponse>({ path: '/api/companion/avatar', method: 'GET' })

      if (!isLive()) {
        return 'portrait-generate'
      }

      const avatar = await applyLocalPortrait(response, isLive)

      if (!isLive()) {
        return 'portrait-generate'
      }

      if (nextField === 'fullbody-reference') {
        return 'fullbody-reference'
      }

      if (avatar && response.id != null) {
        const index = $portraitHistory.get().findIndex(entry => entry.avatarId === response.id)

        if (index >= 0) {
          selectPortraitEntry(index)
        }
      }

      return avatar ? 'portrait-avatar' : 'portrait-generate'
    } catch (error) {
      if (!isLive()) {
        return 'portrait-generate'
      }

      log.warn('onboarding', 'resume portrait failed', error)

      if (nextField === 'fullbody-reference') {
        setPortraitPanelHint('形象恢复失败，请重试')

        return 'portrait-avatar'
      }

      return 'portrait-generate'
    }
  }

  return {
    activeAvatarId,
    portraitSelectedIdx,
    portraitDirectAdopt,
    sealingPortrait,
    portraitUrl,
    portraitPreviewId,
    portraitPanelHint,
    setPortraitPanelHint,
    avatarSelfSourceOpen,
    setAvatarSelfSourceOpen,
    refImage,
    presentationRef,
    setPresentationRef,
    updateRefImage,
    avatarBusy,
    startAiHatching,
    currentHistoryItems,
    onSelectHistoryEntry,
    pickReferenceImage,
    pickPresentationImage,
    fetchAvatarPrompt,
    adoptAvatarSeed,
    sealPortrait,
    continueCurrentPortrait,
    confirmFullbody,
    regenerateAvatarPortrait,
    editAvatarPortrait,
    reloadAvatarPortrait,
    restoreReference,
    restorePortrait
  }
}

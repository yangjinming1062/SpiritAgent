import { useStore } from '@nanostores/react'
import { sleep } from '@runtime'
import * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  assembleCharacterPersona,
  assemblePersona,
  CHARACTER_GENDER_PRESETS,
  clearDraftRefImage,
  clearPortraitHistory,
  FullbodyReferencePanel,
  hydratePortraitHistory,
  loadDraftRefImage,
  MAX_IMAGE_DESCRIPTION,
  MAX_USER_TEXT,
  type OnboardingAnswers,
  patchAvatarSeeds,
  PERSONALITY_PRESETS,
  pickAvatarImage,
  type PickedImage,
  type PortraitEntry,
  pushPortraitEntry,
  RELATIONSHIP_PRESETS,
  saveDraftRefImage,
  selectAvatar,
  selectPortraitEntry,
  SelfSourceImageFlow,
  setCompanionVoiceId,
  SPEAKING_STYLE_PRESETS,
  SPECIES_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from '@/modules/character'
import {
  $voicePreparing,
  fetchVoiceCatalogRaw,
  matchVoicePreference,
  nextVoice,
  sampleLine,
  speakScripted,
  stopSpeaking,
  type VoiceOption,
  VoiceProviderBadge,
  voiceSelectionId,
  warmAudioContext
} from '@/modules/speech'
import { type HistoryGalleryItem, requestGateway } from '@/shared'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { authedApi } from '@/shared/lib/authed-api'
import { FolderOpen, Sparkles } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { backendDetailMessage, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { Chip, DatePicker, INPUT_CLASS, SURFACE_OVERLAY } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'

import { computeBackTransition } from './back-transition'
import { type OnboardingAudioTag, playOnboardingAudio } from './onboarding-audio'
import { PortraitPanel } from './onboarding-components'
import { useRegeneratePortrait } from './use-regenerate-portrait'

type Phase =
  | 'q-character'
  | 'portrait-choose'
  | 'portrait-generate'
  | 'hatching'
  | 'portrait-avatar'
  | 'fullbody-reference'
  | 'q-user'
  | 'voice'
  | 'finishing'

type VoiceStage = 'describe' | 'catalog'

type QKey = keyof OnboardingAnswers

// chip 选的是答案类别而非答案本身——见 CALL_NAME_KINDS。
interface AnswerKind {
  chip: string
  label: string
  placeholder: string
  values?: readonly string[]
}

interface Question {
  key: QKey
  text: string
  placeholder: string
  required: boolean
  multiline: boolean
  // Manifest tag 与录到的语音行绑定而非位置绑定——重排 QUESTIONS 也不会让音频错位。
  audioTag: OnboardingAudioTag
  presets?: readonly string[]
  max?: number
  // 与 `presets` 互斥：双层入口，而不是「点 chip 就把输入框填好」。
  kinds?: readonly AnswerKind[]
  date?: boolean
}

// 「名字/昵称」是称呼类别不是称呼值，点 chip 只换标签再问具体值；「称号」额外给现成选项。
const CALL_NAME_KINDS: readonly AnswerKind[] = [
  { chip: '名字', label: '那，您的名字是？', placeholder: '比如：张三' },
  { chip: '昵称', label: '那，您的昵称是？', placeholder: '比如：小明、阿棠' },
  {
    chip: '称号',
    label: '想让我用哪个称号？',
    placeholder: '或者自己写一个…',
    values: ['老板', '主人', '老师', '大人']
  },
  { chip: '自填', label: '那，想让我怎么叫您？', placeholder: '随便写，我记住就是了…' }
]

const QUESTIONS: readonly Question[] = [
  {
    key: 'name',
    text: '您好…我还不认识自己。您愿意给我一个名字吗？',
    placeholder: '给我起个名字吧',
    required: true,
    multiline: false,
    audioTag: 'onboarding.q0'
  },
  {
    key: 'biological_type',
    text: '那我是哪种生灵呢？',
    placeholder: '或者自由描述…',
    required: true,
    multiline: false,
    audioTag: 'onboarding.q1',
    presets: SPECIES_PRESETS
  },
  {
    key: 'gender',
    text: '嗯…那我是男性、女性、还是…',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q2',
    presets: CHARACTER_GENDER_PRESETS
  },
  {
    key: 'relationship',
    text: '好的，那您希望我是什么样的身份？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q4',
    presets: RELATIONSHIP_PRESETS
  },
  {
    key: 'personality',
    text: '您希望我是什么性格？',
    placeholder: '自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q5',
    presets: PERSONALITY_PRESETS
  },
  // speaking_style 是后端 schema 必填项，用专门一道题收集，与其它角色字段一起进 enterPortraitStage 的 PUT。
  {
    key: 'speaking_style',
    text: '您希望我说话的风格是什么样的？',
    placeholder: '比如：简短、爱用比喻、俏皮一点…',
    required: true,
    multiline: true,
    audioTag: 'onboarding.q10',
    max: 500,
    presets: SPEAKING_STYLE_PRESETS
  },
  {
    key: 'voice',
    text: '您希望我听起来是什么样的？比如温柔的少女音、沉稳的男声、活泼的正太…',
    placeholder: '描述你想要的声音…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q12',
    presets: VOICE_PRESETS
  },
  {
    key: 'user_call_name',
    text: '我该怎么称呼您？',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q6',
    max: MAX_USER_TEXT,
    kinds: CALL_NAME_KINDS
  },
  {
    key: 'user_gender',
    text: '您方便告诉我您的性别吗？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q7',
    max: MAX_USER_TEXT,
    presets: USER_GENDER_PRESETS
  },
  {
    key: 'user_birthday',
    text: '您方便告诉我您的生日吗？',
    placeholder: '选择日期（可不填）',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q8',
    max: MAX_USER_TEXT,
    date: true
  },
  {
    key: 'user_hobbies',
    text: '您平时喜欢什么？',
    placeholder: '可以多写几个…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q9',
    max: MAX_USER_TEXT
  },
  {
    key: 'user_freeform',
    text: '还有什么想告诉我、或者想叮嘱我的吗？',
    placeholder: '可跳过…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q11',
    max: MAX_USER_TEXT
  }
]

// 确认全身形象后固定；外形细节由角色卡维护。
const LOCKED_FIELD_KEYS: ReadonlySet<QKey> = new Set(['biological_type', 'gender'])

const LOCKED_FIELD_LABELS: Partial<Record<QKey, string>> = {
  biological_type: '物种',
  gender: '性别'
}

// 分段边界由 voice 题位置决定：之前是角色子阶段，它是声音子阶段，之后是用户子阶段（对应后端 ONBOARDING_FIELDS 顺序）。
const VOICE_Q_INDEX = QUESTIONS.findIndex(q => q.key === 'voice')
const CHARACTER_QUESTIONS: readonly Question[] = QUESTIONS.slice(0, VOICE_Q_INDEX)
const VOICE_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX, VOICE_Q_INDEX + 1)
const USER_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX + 1)

const PHASE_QUESTIONS: Record<Phase, readonly Question[]> = {
  'q-character': CHARACTER_QUESTIONS,
  'q-user': USER_QUESTIONS,
  voice: VOICE_QUESTIONS,
  'portrait-choose': [],
  'portrait-generate': [],
  hatching: [],
  'portrait-avatar': [],
  'fullbody-reference': [],
  finishing: []
}

// resume 的 next_field 路由到 q-user（voice 另有分支）；从 USER_QUESTIONS 推导，题目增删自动同步。
const POST_CHARACTER_FIELDS: ReadonlySet<string> = new Set(USER_QUESTIONS.map(q => q.key))

// 提到顶层：否则 useInteractiveRegion 的 effect 会在每次渲染时重新注册。
const interactiveRegionRect = (el: HTMLElement): DOMRect | null => {
  const rect = el.getBoundingClientRect()

  return rect.width === 0 || rect.height === 0 ? null : rect
}

// `fn` 抛出的错误会向上传播，方便调用方把 4xx 重新抛出并提前结束重试。
const retryTransient = async <T,>(
  fn: () => Promise<T | null | undefined>,
  delayMs: number,
  maxAttempts = 3
): Promise<T | null> => {
  for (let i = 0; i < maxAttempts; i++) {
    const result = await fn()

    if (result) {
      return result
    }

    if (i < maxAttempts - 1) {
      await sleep(delayMs)
    }
  }

  return null
}

const DRAG_THRESHOLD = 6

// 可经 onboarding.submit 提交的 key，与后端 ONBOARDING_FIELDS 对齐（恒等映射，故用 Set）。
const ONBOARDING_FIELD_KEYS: ReadonlySet<QKey> = new Set<QKey>([
  'name',
  'biological_type',
  'gender',
  'relationship',
  'personality',
  'speaking_style',
  'user_call_name',
  'user_gender',
  'user_birthday',
  'user_hobbies',
  'user_freeform',
  'voice'
])

async function savePersona(payload: ReturnType<typeof assemblePersona>): Promise<boolean> {
  try {
    await window.spiritagent.api({
      path: '/api/companion/persona',
      method: 'PUT',
      body: { definition_json: JSON.stringify(payload) }
    })

    return true
  } catch (error) {
    // 重抛 4xx，避免 retryTransient 在确定性失败上空耗重试。
    if (isClientErrorIpc(error)) {
      throw error
    }

    log.warn('onboarding', 'persona save failed', error)

    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

// 放到 OnboardingFlow 外面：否则它订阅的 $regenFeedback 会在每次按键时让整个对话框重渲染。
function RegenFeedbackInput({ id, initial = false }: { id?: string; initial?: boolean }): React.JSX.Element {
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      id={id}
      maxLength={MAX_IMAGE_DESCRIPTION}
      onChange={e => $regenFeedback.set(e.target.value)}
      placeholder={
        initial
          ? '比如：金发绿眼、额间一道疤、机械义眼…（可留空）'
          : '哪里不满意？比如：头发再短一点、眼睛再大一点、表情更温和…（可留空直接重新生成）'
      }
      rows={2}
      value={value}
    />
  )
}

// 同 RegenFeedbackInput：微调按钮的 disabled 依赖反馈非空，订阅放独立小组件避免整框重渲染。
function EditAvatarButton({
  busy,
  disabledByReference,
  onEdit
}: {
  busy: boolean
  disabledByReference: boolean
  onEdit: () => void
}): React.JSX.Element {
  const feedback = useStore($regenFeedback)
  const disabled = busy || disabledByReference || !feedback.trim()

  return (
    <button
      className="text-body transition hover:text-strong disabled:opacity-40"
      disabled={disabled}
      onClick={onEdit}
      title={
        disabledByReference ? '附参考图时不可微调，请先移除参考图' : '在当前头像上修改，其余保持不变（需先填写反馈）'
      }
      type="button"
    >
      微调
    </button>
  )
}

function SpinnerWithText({ text, size = 'h-5 w-5' }: { text: string; size?: string }): React.JSX.Element {
  return (
    <div className="flex flex-col items-center gap-2 py-4">
      <div className={`${size} animate-spin rounded-full border-2 border-line-strong border-t-accent`} />
      <p className="text-sm text-body">{text}</p>
    </div>
  )
}

export function OnboardingFlow({ onCompleted }: OnboardingFlowProps): React.JSX.Element | null {
  const gatewayState = useStore($gatewayState)
  const voicePreparing = useStore($voicePreparing)
  const [phase, setPhase] = useState<Phase>('q-character')
  // 身份锁定后禁止返回形象确认步骤。
  const [imageSealed, setImageSealed] = useState(false)
  const [portraitDirectAdopt, setPortraitDirectAdopt] = useState(false)
  // /portrait/confirm 在途时禁用头像阶段全部操作，避免确认结果与并发生成、返回竞争。
  const [sealingPortrait, setSealingPortrait] = useState(false)
  const [qIndex, setQIndex] = useState(0)
  const onboardingSubmissionsRef = useRef(Promise.resolve())
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [portraitPreviewId, setPortraitPreviewId] = useState<number | null>(null)
  const mountedRef = useRef(false)
  const activeAvatarId = useStore($activeAvatarId)
  const portraitHistory = useStore($portraitHistory)
  const portraitSelectedIdx = useStore($portraitSelectedIdx)
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('describe')

  // 失败时保留当前头像：它已持有解析好的字节。
  const applyLocalPortrait = async (
    response:
      | {
          asset_url?: string | null
          id?: number
        }
      | null
      | undefined
  ): Promise<{ assetUrl: string | null; avatar: string | null; id: number | null }> => {
    const { avatar } = await applyPortrait(
      { id: response?.id, assetUrl: response?.asset_url },
      () => mountedRef.current
    )

    if (avatar) {
      setPortraitUrl(avatar)
      setPortraitPreviewId(response?.id ?? null)
    }

    return { assetUrl: response?.asset_url ?? null, avatar, id: response?.id ?? null }
  }

  const [voice, setVoice] = useState<VoiceOption | null>(null)
  // 目录步骤的加载状态：失败时保留已选音色并提供重试，不能显示成「没有可用音色」。
  const [voiceLoad, setVoiceLoad] = useState<'failed' | 'loading' | 'ready'>('loading')
  const [voiceLoadAttempt, setVoiceLoadAttempt] = useState(0)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  // 匹配器候选项，与完整目录分开，让「换一个」在候选项内循环而非遍历全目录。
  const [voiceAlternatives, setVoiceAlternatives] = useState<VoiceOption[]>([])
  // 失败提示挂在头像面板上——表单区被它压在下面。
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)
  const [avatarSelfSourceOpen, setAvatarSelfSourceOpen] = useState(false)

  // 身份参考图持久化为草稿，供引导重启恢复。
  const [refImage, setRefImage] = useState<PickedImage | null>(null)

  // 光线与构图参考仅供本次重绘，保留在内存中。
  const [presentationRef, setPresentationRef] = useState<PickedImage | null>(null)

  const updateRefImage = (img: PickedImage | null): void => {
    setRefImage(img)
    void saveDraftRefImage(img)
  }

  const [answerKind, setAnswerKind] = useState<AnswerKind | null>(null)

  const [hint, setHint] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const resumedRef = useRef(false)
  // 读不到服务端进度时暂停作答，避免新回答覆盖尚未读回的草稿。
  const [resumeState, setResumeState] = useState<'failed' | 'ok' | 'retrying'>('ok')
  const [resumeAttempt, setResumeAttempt] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  const [dialogPos, setDialogPos] = useState<{ x: number; y: number }>(() => {
    const width = 448
    const height = 600

    return {
      x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(0, Math.round((window.innerHeight - height) / 2))
    }
  })

  // 注册对话框可见矩形到 interactive-regions，SpriteStage 命中测试只在表单卡片上捕获；卸载时恢复穿透。
  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      stopSpeaking()
    }
  }, [])

  // q1 之前预热 ctx，避免 MediaElementSource 重路由吃掉首帧。
  useEffect(() => {
    warmAudioContext()
  }, [])

  // document 级监听让指针离开对话框后仍可拖拽；基准位与实时位移分离，dialogPos 仅松手时提交，避免位移叠到不断重设的 origin 上加速漂移。
  const { delta: dialogDragDelta, onPointerDown: onRawDialogPointerDown } = usePointerDrag({
    threshold: DRAG_THRESHOLD,
    onCommit: ({ dx, dy }) => {
      setDialogPos(prev => ({ x: prev.x + dx, y: prev.y + dy }))
    }
  })

  const dialogLeft = dialogPos.x + dialogDragDelta.dx
  const dialogTop = dialogPos.y + dialogDragDelta.dy

  // 表单控件交由浏览器原生——若起点是按钮/输入框/可编辑元素则不进入拖拽。
  const onDialogPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    const target = e.target as HTMLElement

    if (target.closest('button, input, textarea, [contenteditable="true"]')) {
      return
    }

    onRawDialogPointerDown(e)
  }

  const currentList = PHASE_QUESTIONS[phase]

  const question = currentList[qIndex]
  // 用 ref 持有最新 answers，使 effect 只在 phase/qIndex 变化时重跑而非每次按键（exhaustive-deps 看不到该意图）。
  const answersRef = useLatestRef(answers)

  const spokenText = question?.text ?? ''

  // 保存失败退回题目时带回的提示：换题重置不能清掉它，此时不重读题面。
  const carriedHintRef = useRef<string | null>(null)

  useEffect(() => {
    if (phase !== 'q-character' && phase !== 'q-user' && phase !== 'voice') {
      return
    }

    const q = currentList[qIndex]

    if (!q) {
      return
    }

    const current = answersRef.current
    const initialVal = (current[q.key] as string) ?? ''
    const carriedHint = carriedHintRef.current
    carriedHintRef.current = null
    setInput(initialVal)
    setAnswerKind(null)
    setHint(carriedHint)

    if (!carriedHint) {
      void playOnboardingAudio(q.audioTag)
    }

    return () => stopSpeaking()
  }, [phase, qIndex, currentList, answersRef])

  // 结果为是否已提交。单字段失败不阻断作答：角色与用户资料随整体保存提交，音色在完成前重新提交并核对。
  const submitOnboardingAnswer = useCallback((field: QKey, value: string | null): Promise<boolean> => {
    const submission = onboardingSubmissionsRef.current.then(async () => {
      try {
        await requestGateway('onboarding.submit', { field, value })

        return true
      } catch (error) {
        log.warn('onboarding', `onboarding.submit ${field} failed`, error)

        return false
      }
    })

    onboardingSubmissionsRef.current = submission.then(() => undefined)

    return submission
  }, [])

  useEffect(() => {
    const isQuestionPhase = phase === 'q-character' || phase === 'q-user' || phase === 'voice'

    if (isQuestionPhase && currentList[qIndex]) {
      ;(currentList[qIndex].multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, qIndex, currentList])

  const commit = (value: string | undefined): OnboardingAnswers => {
    const q = currentList[qIndex]

    if (!q) {
      return answers
    }

    const trimmed = value && value.trim() ? value.trim() : undefined
    const cleaned = trimmed && q.max ? trimmed.slice(0, q.max) : trimmed
    const nextAnswers: OnboardingAnswers = { ...answers, [q.key]: cleaned }
    setAnswers(nextAnswers)

    // 逐字段增量持久化（DESIGN 断点恢复），fire-and-forget 不阻塞 UI；网关未打开前是空操作。
    if (gatewayState === 'open' && ONBOARDING_FIELD_KEYS.has(q.key)) {
      void submitOnboardingAnswer(q.key, cleaned ?? null)
    }

    return nextAnswers
  }

  const advance = (updatedAnswers?: OnboardingAnswers): void => {
    const currentAnswers = updatedAnswers ?? answers

    // Voice describe 只有一道题；点下一题会切到 catalog，由下面的 useEffect 加载。
    if (phase === 'voice' && voiceStage === 'describe') {
      setVoiceStage('catalog')

      return
    }

    if (qIndex < currentList.length - 1) {
      setQIndex(qIndex + 1)

      return
    }

    if (phase === 'q-character') {
      void enterPortraitStage(currentAnswers)
    } else if (phase === 'q-user') {
      setPhase('finishing')
      void finish(currentAnswers)
    }
  }

  // 进入目录步骤时加载推荐与目录并试听；离开后迟到结果作废：不改选音色、不上云、不在其他步骤出声。
  useEffect(() => {
    if (phase !== 'voice' || voiceStage !== 'catalog') {
      return
    }

    let cancelled = false
    stopSpeaking()
    setVoiceLoad('loading')

    void (async () => {
      const [matched, result] = await Promise.all([
        matchVoicePreference(requestGateway, answers.voice ?? ''),
        fetchVoiceCatalogRaw(requestGateway)
      ])

      if (cancelled) {
        return
      }

      // 请求失败不能当作「没有匹配」改选目录首项；保留已选音色，等待重试。
      if (!matched.ok || !result.ok) {
        if (!result.ok) {
          log.warn('onboarding', 'voice catalog request failed')
        }

        setVoiceLoad('failed')

        return
      }

      const catalog = result.catalog.voices
      const selected = matched.voice ?? catalog[0] ?? null
      setVoice(selected)
      setVoiceAlternatives(matched.alternatives)
      setCompanionVoiceId(selected ? voiceSelectionId(selected) : '')
      // matched voice 与 alternatives 已前置，需从目录剔除，否则同音色（如「茉莉」）会在列表重复。
      const priorityVoices = selected ? [selected, ...matched.alternatives] : []
      const priorityIds = new Set(priorityVoices.map(voiceSelectionId))
      const extra = catalog.filter(v => !priorityIds.has(voiceSelectionId(v)))
      setVoiceCatalog([...priorityVoices, ...extra])
      setVoiceLoad('ready')

      if (!selected) {
        return
      }

      void speakScripted(sampleLine(answers.name || ''), voiceSelectionId(selected), 'onboarding.voice.preview')
    })()

    return () => {
      cancelled = true
    }
  }, [phase, voiceStage, answers.voice, answers.name, voiceLoadAttempt])

  const onSend = (): void => {
    const q = currentList[qIndex]

    if (q?.required && !input.trim()) {
      const requiredHints: Record<string, string> = {
        name: '名字是必填的哦～',
        biological_type: '生灵类型是必填的哦～',
        speaking_style: '说话风格是必填的哦～'
      }

      setHint(requiredHints[q.key] ?? '此项是必填的哦～')

      return
    }

    const nextAnswers = commit(input)
    advance(nextAnswers)
  }

  const onSkip = (): void => {
    if (question?.required) {
      return
    }

    const nextAnswers = commit(undefined)
    advance(nextAnswers)
  }

  const onBack = (): void => {
    // 形象确认后不能再返回形象步骤：``computeBackTransition`` 在 imageSealed 时只允许音色与用户资料步骤之间回退。
    const intent = computeBackTransition(
      { phase, qIndex, voiceStage, imageSealed, portraitDirectAdopt },
      CHARACTER_QUESTIONS.length
    )

    if (!intent) {
      return
    }

    if (intent.phase !== phase) {
      setPhase(intent.phase)
    }

    if (intent.qIndex !== undefined && intent.qIndex !== qIndex) {
      setQIndex(intent.qIndex)
    }

    if (intent.voiceStage !== undefined && intent.voiceStage !== voiceStage) {
      setVoiceStage(intent.voiceStage)
    }
  }

  const enterPortraitStage = async (currentAnswers?: OnboardingAnswers): Promise<void> => {
    // 形象已锁死时不应再进入头像/全身图阶段。深度防御:onBack 守卫 + 此处显式短路,即使上游误调也无效。
    if (imageSealed) {
      return
    }

    const ans = currentAnswers ?? answers
    setHint(null)

    // 先固化 persona 再进入头像阶段——让用户在「AI 生成」与「直接上传」两条入口里选。
    let personaOk = false
    await onboardingSubmissionsRef.current

    try {
      personaOk = (await retryTransient(() => savePersona(assembleCharacterPersona(ans)), 700)) === true
    } catch (err) {
      log.warn('onboarding', 'character persona save rejected', err)
      setPhase('q-character')
      setHint(backendDetailMessage(err, '角色资料保存失败，请检查填写内容后重试'))

      return
    }

    if (!personaOk) {
      setHint('角色资料保存失败，请检查网络后重试')

      return
    }

    setPhase('portrait-choose')
  }

  const startAiHatching = async (): Promise<void> => {
    if (imageSealed) {
      return
    }

    setPhase('hatching')
    setPortraitDirectAdopt(false)
    setPortraitPanelHint(null)
    setHint(null)

    const succeeded = await generateAvatarPortrait()

    if (succeeded === null) {
      return
    }

    // 失败时同样进入确认步骤，由头像面板显示原因。
    setPhase('portrait-avatar')
  }

  // 网关连通后拉回未答草稿，支持中断后从下一未答题继续；成功后不重复，读取失败暂停作答待重试。
  const onCompletedRef = useLatestRef(onCompleted)

  useEffect(() => {
    if (resumedRef.current || gatewayState !== 'open') {
      return
    }

    resumedRef.current = true

    void (async () => {
      const markResumeFailed = (): void => {
        resumedRef.current = false
        setResumeState('failed')
      }

      try {
        const cachedRef = await loadDraftRefImage()

        if (cachedRef) {
          setRefImage(cachedRef)
        }

        let state: {
          answers?: Record<string, string>
          next_field?: string | null
          complete?: boolean
        } | null = null

        try {
          state = await window.spiritagent.api<{
            answers?: Record<string, string>
            next_field?: string | null
            complete?: boolean
          }>({
            path: '/api/companion/onboarding/state'
          })
        } catch (error) {
          log.warn('onboarding', 'resume state REST failed', error)
          state = await requestGateway<{
            answers?: Record<string, string>
            next_field?: string | null
            complete?: boolean
          }>('onboarding.get_state', {}).catch((gatewayError: unknown) => {
            log.warn('onboarding', 'resume state gateway failed', gatewayError)

            return null
          })
        }

        // 没读到服务端进度不能当作新引导，否则新回答会覆盖已保存的草稿。
        if (!state) {
          markResumeFailed()

          return
        }

        setResumeState('ok')

        if (state.complete) {
          void clearDraftRefImage()
          onCompletedRef.current()

          return
        }

        if (state.answers) {
          // 合并服务端草稿与本地答案，本地非空编辑优先，避免丢失用户最近意图。
          const a = state.answers
          setAnswers(prev => {
            const next: OnboardingAnswers = { ...prev }

            for (const k of Object.keys(a) as (keyof OnboardingAnswers)[]) {
              if (next[k] == null || next[k] === '') {
                next[k] = a[k] as never
              }
            }

            return next
          })

          const nextField = state.next_field

          if (nextField === 'portrait') {
            try {
              await hydratePortraitHistory()

              const avatarRes = await window.spiritagent.api<{
                asset_url?: string | null
                id?: number
              }>({
                path: '/api/companion/avatar',
                method: 'GET'
              })

              const applied = await applyLocalPortrait(avatarRes)

              if (applied.avatar) {
                if (avatarRes?.id != null) {
                  const idx = $portraitHistory.get().findIndex(e => e.avatarId === avatarRes.id)

                  if (idx >= 0) {
                    selectPortraitEntry(idx)
                  }
                }

                setPhase('portrait-avatar')
              } else {
                setPhase('portrait-choose')
              }
            } catch (error) {
              log.warn('onboarding', 'resume portrait failed', error)
              setPhase('portrait-choose')
            }
          } else if (nextField === 'fullbody-reference') {
            try {
              const avatarRes = await window.spiritagent.api<{ id: number; asset_url: string }>({
                path: '/api/companion/avatar'
              })

              await applyLocalPortrait(avatarRes)
              setPhase('fullbody-reference')
            } catch (error) {
              log.warn('onboarding', 'resume fullbody failed', error)
              setPhase('portrait-avatar')
              setPortraitPanelHint('形象恢复失败，请重试')
            }
          } else if (nextField === 'voice') {
            // next_field==='voice' 意味着描述句本身还没回答——落在 describe 上，而不是 catalog。
            setImageSealed(true)
            setPhase('voice')
            setVoiceStage('describe')
            setQIndex(0)
          } else if (nextField && POST_CHARACTER_FIELDS.has(nextField)) {
            setImageSealed(true)
            const idx = USER_QUESTIONS.findIndex(q => q.key === nextField)
            setPhase('q-user')
            setQIndex(Math.max(0, idx))
          } else if (nextField) {
            const idx = CHARACTER_QUESTIONS.findIndex(q => q.key === nextField)
            setPhase('q-character')
            setQIndex(Math.max(0, idx))
          }
        }
      } catch (error) {
        log.warn('onboarding', 'resume failed', error)
        markResumeFailed()

        return
      }

      const r = await fetchVoiceCatalogRaw(requestGateway)

      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }
    })()
  }, [gatewayState, onCompletedRef, resumeAttempt])

  useEffect(() => {
    if (gatewayState !== 'open' || voiceCatalog.length > 0) {
      return
    }

    void fetchVoiceCatalogRaw(requestGateway).then(r => {
      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }
    })
  }, [gatewayState, voiceCatalog.length])

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

  const pickReferenceImage = async (): Promise<void> => {
    const picked = await pickAvatarImage('选择一张参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    updateRefImage(picked.image)
    setHint(null)
  }

  const fetchAvatarPrompt = async (): Promise<string> => {
    const response = await window.spiritagent.api<{ prompt: string }>({
      path: '/api/companion/avatar/prompt',
      method: 'POST',
      body: { feedback: $regenFeedback.get().trim() || undefined, has_reference: Boolean(refImage) }
    })

    return response.prompt
  }

  const adoptAvatarSeed = async (image: PickedImage): Promise<void> => {
    const epoch = currentClearEpoch()

    const response = await window.spiritagent.api<{ id: number; asset_url: string }>({
      path: '/api/companion/avatar/adopt',
      method: 'POST',
      body: { image: image.base64, content_type: image.contentType }
    })

    if (!mountedRef.current || currentClearEpoch() !== epoch) {
      return
    }

    const applied = await applyLocalPortrait(response)

    if (!mountedRef.current || currentClearEpoch() !== epoch) {
      return
    }

    if (!applied.avatar) {
      throw new Error('头像已保存，预览加载失败，请重新加载')
    }

    pushPortraitEntry({ assetUrl: applied.assetUrl, avatarId: applied.id, portraitUrl: applied.avatar })
    $regenFeedback.set('')
    setPresentationRef(null)
    setPortraitPanelHint(null)
    // 自备图即心仪头像，采纳后直接确认。
    setPortraitDirectAdopt(true)
    await sealPortrait(applied.id)
  }

  const pickPresentationImage = async (): Promise<void> => {
    const picked = await pickAvatarImage('选择光线与构图参考图')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setHint(picked.error)

      return
    }

    setPresentationRef(picked.image)
    setHint(null)
  }

  // 确认头像并进入全身阶段；失败落到确认步骤展示原因，可原地重试。
  const sealPortrait = async (expectedAvatarId: number | null = portraitPreviewId): Promise<void> => {
    if (sealingPortrait || expectedAvatarId === null) {
      return
    }

    setSealingPortrait(true)

    try {
      const result = await authedApi({
        path: '/api/companion/portrait/confirm',
        method: 'POST',
        body: { expected_avatar_id: expectedAvatarId }
      })

      if (!mountedRef.current || (!result.ok && result.reason === 'unauth')) {
        return
      }

      if (!result.ok) {
        throw result.error
      }
    } catch (error) {
      // 确认失败（如 409 头像已更新或临时文件过期）绝不能推进：退回确认步骤说明原因；onClick 的 void 会吞异常，故这里显式提示。
      log.warn('onboarding', 'portrait confirm failed', error)
      setPortraitPanelHint(backendDetailMessage(error, '确认失败，请检查网络后重试'))
      setPhase('portrait-avatar')

      return
    } finally {
      setSealingPortrait(false)
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

    const epoch = currentClearEpoch()

    const res = await window.spiritagent.api<{
      id: number
      asset_url: string
      seed_fullbody_url: string
    }>({
      path: `/api/companion/avatar/${activeAvatarId}/fullbody/confirm`,
      method: 'POST',
      body: { expected_url: expectedUrl }
    })

    if (currentClearEpoch() !== epoch || $activeAvatarId.get() !== activeAvatarId) {
      return
    }

    await applyLocalPortrait(res)

    if (currentClearEpoch() !== epoch) {
      return
    }

    await patchAvatarSeeds({ avatarId: res.id, fullbodySeedUrl: res.seed_fullbody_url })

    if (currentClearEpoch() !== epoch) {
      return
    }

    setImageSealed(true)
    setPhase('voice')
    setVoiceStage('describe')
    setQIndex(0)
    setInput('')
    setAnswerKind(null)
    setHint(null)
  }

  const previewVoice = (next: VoiceOption, context: string): void =>
    void speakScripted(sampleLine(answers.name || ''), voiceSelectionId(next) || undefined, context)

  // 选中时总要试听：标签本身说明不了声音听起来什么样。
  const selectVoice = (next: VoiceOption, context: string): void => {
    setVoice(next)
    setCompanionVoiceId(voiceSelectionId(next))
    previewVoice(next, context)
  }

  const confirmVoice = (): void => {
    if (voice) {
      const vName = voice.label || voice.id
      setAnswers(prev => ({ ...prev, voice: vName }))
      void submitOnboardingAnswer('voice', vName)
    }

    setPhase('q-user')
    setQIndex(0)
    setInput('')
    setAnswerKind(null)
    setHint(null)
  }

  const finish = async (currentAnswers?: OnboardingAnswers): Promise<void> => {
    const ans = { ...answers, ...(currentAnswers ?? {}) }

    if (voice && !ans.voice) {
      ans.voice = voice.label || voice.id
    }

    // 服务端以音色草稿与完整资料判定完成：暂时性失败有限重试，仍失败退回最后一题提示，不清草稿不标记完成。
    let failure: string | null = null

    try {
      const voiceSaved =
        !voice || (await retryTransient(() => submitOnboardingAnswer('voice', voice.label || voice.id), 700)) === true

      await onboardingSubmissionsRef.current

      const saved = voiceSaved && (await retryTransient(() => savePersona(assemblePersona(ans)), 700)) === true

      if (!saved) {
        failure = '资料保存失败，请检查网络后再点「完成」重试'
      }
    } catch (err) {
      log.warn('onboarding', 'final persona save rejected', err)
      failure = backendDetailMessage(err, '资料保存失败，请检查填写内容后重试')
    }

    if (failure) {
      // 从 finishing 退回题目会触发换题重置，提示经 carriedHintRef 带过去。
      carriedHintRef.current = failure
      setPhase('q-user')
      setQIndex(USER_QUESTIONS.length - 1)

      return
    }

    void clearDraftRefImage()
    updateRefImage(null)
    // 初次问候由伙伴在后端主动回合中生成并经陪伴消息送达，引导不等待也不代写台词。
    onCompleted()
  }

  const presetValues = question?.presets ?? []
  const otherVoices = voice ? voiceCatalog.filter(v => voiceSelectionId(v) !== voiceSelectionId(voice)) : []
  const voiceCandidates = voice ? [voice, ...(voiceAlternatives.length ? voiceAlternatives : otherVoices)] : []

  const canGoBack =
    computeBackTransition(
      { phase, qIndex, voiceStage, imageSealed, portraitDirectAdopt },
      CHARACTER_QUESTIONS.length
    ) !== null

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <div
        className="absolute flex max-h-[90vh] w-full max-w-md flex-col items-center gap-4"
        onPointerDown={onDialogPointerDown}
        ref={containerRef}
        style={{
          left: dialogLeft,
          padding: '0 1.5rem',
          pointerEvents: 'auto',
          position: 'absolute',
          top: dialogTop,
          touchAction: 'none'
        }}
      >
        <div className={`w-full rounded-2xl p-5 text-strong ${SURFACE_OVERLAY}`} style={{ pointerEvents: 'auto' }}>
          {voicePreparing && <p className="mb-2 text-center text-[10px] text-muted">正在准备声音…</p>}
          {resumeState !== 'ok' && (
            <div className="py-2 text-center">
              <p className="text-sm text-body">没能读取之前保存的进度，请检查网络后重试。</p>
              <button
                className="mt-3 inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                disabled={resumeState === 'retrying'}
                onClick={() => {
                  setResumeState('retrying')
                  setResumeAttempt(n => n + 1)
                }}
                type="button"
              >
                {resumeState === 'retrying' ? '正在重试…' : '重试'}
              </button>
            </div>
          )}
          {resumeState === 'ok' && phase === 'q-character' && question && LOCKED_FIELD_KEYS.has(question.key) && (
            <p className="mb-2 rounded-md border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-[10px] leading-relaxed text-strong">
              「{LOCKED_FIELD_LABELS[question.key] ?? '当前字段'}」是形象确认后无法再次更改的重点内容，请仔细选择。
            </p>
          )}
          {resumeState === 'ok' &&
            (phase === 'q-character' || phase === 'q-user' || (phase === 'voice' && voiceStage === 'describe')) &&
            question && (
              <>
                <p className="min-h-[3.5rem] text-[15px] leading-relaxed">{spokenText}</p>
                {presetValues.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {presetValues.map(p => (
                      <Chip active={input === p} key={p} label={p} onClick={() => setInput(p)} />
                    ))}
                  </div>
                )}
                {question.kinds && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {question.kinds.map(k => (
                      <Chip
                        active={answerKind?.chip === k.chip}
                        key={k.chip}
                        label={k.chip}
                        onClick={() => {
                          setAnswerKind(k)
                          setInput('')
                          inputRef.current?.focus()
                        }}
                      />
                    ))}
                  </div>
                )}
                {answerKind && (
                  <>
                    <p className="mt-3 text-xs text-body">{answerKind.label}</p>
                    {answerKind.values && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {answerKind.values.map(v => (
                          <Chip active={input === v} key={v} label={v} onClick={() => setInput(v)} />
                        ))}
                      </div>
                    )}
                  </>
                )}
                {question.multiline ? (
                  <textarea
                    className={`mt-3 ${INPUT_CLASS} text-sm`}
                    onChange={e => setInput(e.target.value)}
                    placeholder={question.placeholder}
                    ref={textareaRef}
                    rows={3}
                    value={input}
                  />
                ) : question.date ? (
                  <DatePicker
                    className="mt-3"
                    clearLabel="清除"
                    onChange={setInput}
                    placeholder={question.placeholder}
                    value={input}
                  />
                ) : (
                  <input
                    className={cn(INPUT_CLASS, 'mt-3 text-sm')}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && !question.multiline) {
                        onSend()
                      }
                    }}
                    placeholder={answerKind?.placeholder ?? question.placeholder}
                    ref={inputRef}
                    value={input}
                  />
                )}
                <div className="mt-4 flex items-center justify-between text-xs">
                  <button
                    className="text-body transition hover:text-strong disabled:opacity-30"
                    disabled={!canGoBack}
                    onClick={onBack}
                    type="button"
                  >
                    上一题
                  </button>
                  <div className="flex gap-3">
                    {!question.required && (
                      <button className="text-body transition hover:text-strong" onClick={onSkip} type="button">
                        跳过
                      </button>
                    )}
                    <button
                      className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                      onClick={onSend}
                      type="button"
                    >
                      {qIndex === currentList.length - 1 ? '完成' : '发送'}
                    </button>
                  </div>
                </div>
                {hint && <p className="mt-2 text-xs text-strong">{hint}</p>}
                <p className="mt-2 text-right text-[10px] text-faint">
                  {qIndex + 1} / {currentList.length}
                </p>
              </>
            )}

          {phase === 'portrait-choose' && (
            <div>
              <p className="text-[15px] font-medium text-strong">选择头像获取方式</p>
              <p className="mt-1 text-xs text-body">
                先为伙伴确定头像，再准备全身形象。可以让 AI 绘制，也可以直接上传您准备好的图片。
              </p>

              <div className="mt-4 flex flex-col gap-3">
                <button
                  className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
                  disabled={sealingPortrait}
                  onClick={() => setPhase('portrait-generate')}
                  type="button"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                      <Sparkles className="size-4 text-muted" /> AI 生成
                    </span>
                    <span className="text-xs text-muted">AI 绘制 →</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                    可在生成前补充头像描述与参考图，之后预览、微调或重新生成。
                  </p>
                </button>

                <button
                  className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
                  disabled={avatarBusy || sealingPortrait}
                  onClick={() => setAvatarSelfSourceOpen(true)}
                  type="button"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                      <FolderOpen className="size-4 text-muted" /> 直接上传
                    </span>
                    <span className="text-xs text-muted">选择图片 →</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                    上传一张您准备好的图片作为头像；也可以获取提示词，在外部绘制后上传。
                  </p>
                </button>

                {activeAvatarId != null && portraitUrl && (
                  <button
                    className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
                    disabled={avatarBusy || sealingPortrait}
                    onClick={() => void continueCurrentPortrait()}
                    type="button"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[14px] font-medium text-strong">继续使用当前头像</span>
                      <span className="text-xs text-muted">已有草稿 →</span>
                    </div>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                      沿用之前的头像草稿，直接进入全身形象准备。
                    </p>
                  </button>
                )}
              </div>

              {portraitPanelHint && <p className="mt-3 text-xs text-danger-fg">{portraitPanelHint}</p>}

              <div className="mt-4 flex items-center justify-between text-xs">
                <button
                  className="text-body transition hover:text-strong disabled:opacity-30"
                  disabled={sealingPortrait}
                  onClick={onBack}
                  type="button"
                >
                  上一步
                </button>
              </div>
            </div>
          )}

          {phase === 'portrait-generate' && (
            <div>
              <p className="text-[15px] font-medium text-strong">用 AI 生成头像</p>
              <p className="mt-1 text-xs text-body">
                可以描述希望头像呈现的面容、发型、颜色和辨识细节，也可以附加参考图。留空即可按角色设定生成。
              </p>

              <div className="mt-3 space-y-1">
                <label className="text-xs text-body" htmlFor="onboarding-portrait-description">
                  头像描述（可选）
                </label>
                <RegenFeedbackInput id="onboarding-portrait-description" initial />
              </div>

              <div className="mt-3 rounded-xl border border-line-hairline bg-fill-trough p-3">
                <p className="text-xs text-body">参考图（可选）</p>
                <p className="mt-1 text-[11px] text-muted">用于生成时借鉴外形，不会直接成为头像。</p>
                <div className="mt-2 flex items-center gap-2">
                  {refImage && (
                    <img alt="生成参考图" className="size-12 rounded-md object-contain" src={refImage.previewUrl} />
                  )}
                  <button className="text-xs text-strong" onClick={() => void pickReferenceImage()} type="button">
                    {refImage ? '更换参考图' : '添加参考图'}
                  </button>
                  {refImage && (
                    <button className="text-xs text-muted" onClick={() => updateRefImage(null)} type="button">
                      移除
                    </button>
                  )}
                </div>
                {hint && <p className="mt-2 text-xs text-strong">{hint}</p>}
              </div>

              <div className="mt-4 flex items-center justify-between text-xs">
                <button
                  className="text-body transition hover:text-strong disabled:opacity-40"
                  disabled={sealingPortrait}
                  onClick={() => {
                    setHint(null)
                    setPhase('portrait-choose')
                  }}
                  type="button"
                >
                  上一步
                </button>
                <button
                  className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                  disabled={sealingPortrait}
                  onClick={() => void startAiHatching()}
                  type="button"
                >
                  开始生成
                </button>
              </div>
            </div>
          )}

          <SelfSourceImageFlow
            adopt={adoptAvatarSeed}
            fetchPrompt={fetchAvatarPrompt}
            hint="请使用单个角色、纯白背景的图片，采纳后将直接作为头像。"
            onClose={() => setAvatarSelfSourceOpen(false)}
            onUseAi={() => {
              setAvatarSelfSourceOpen(false)
              setHint(null)
              setPhase('portrait-generate')
            }}
            open={avatarSelfSourceOpen}
            referenceImages={refImage ? [{ label: '形象参考图', url: refImage.previewUrl }] : undefined}
            referenceRequired={Boolean(refImage)}
            title="直接上传"
          />

          {phase === 'hatching' && <SpinnerWithText size="h-6 w-6" text={hint || '正在生成头像…'} />}

          {phase === 'portrait-avatar' && (
            <PortraitPanel
              avatarUrl={portraitUrl}
              hint={portraitPanelHint}
              history={currentHistoryItems}
              introHint="预览并确认您的头像"
              name={answers.name?.trim() || '伙伴'}
              onSelectEntry={onSelectHistoryEntry}
              selectedIdx={portraitSelectedIdx}
            />
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              {avatarBusy ? (
                <SpinnerWithText text="正在重新生成头像…" />
              ) : (
                <>
                  <RegenFeedbackInput />
                  <div className="mt-2 space-y-2 text-xs">
                    {refImage && (
                      <div className="flex items-center gap-2">
                        <img alt="形象参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                        <span className="text-[10px] text-faint">形象参考图（每次重生都会携带）</span>
                        <button
                          className="ml-auto text-muted transition hover:text-strong"
                          onClick={() => updateRefImage(null)}
                          type="button"
                        >
                          移除
                        </button>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        className="rounded-full border border-dashed border-line-standard px-3 py-1 text-body transition hover:bg-fill-hover"
                        onClick={() => void pickPresentationImage()}
                        type="button"
                      >
                        {presentationRef ? '更换构图参考' : '＋ 光线与构图参考'}
                      </button>
                      {presentationRef && (
                        <>
                          <img
                            alt="光线与构图参考"
                            className="h-9 w-9 rounded-md object-cover"
                            src={presentationRef.previewUrl}
                          />
                          <span className="text-[10px] text-faint">参考光线与构图，沿用统一视觉风格</span>
                          <button
                            className="ml-auto text-muted transition hover:text-strong"
                            onClick={() => setPresentationRef(null)}
                            type="button"
                          >
                            移除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs">
                    <div className="flex flex-wrap gap-3">
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        disabled={sealingPortrait}
                        onClick={onBack}
                        type="button"
                      >
                        上一步
                      </button>
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        disabled={avatarBusy || sealingPortrait}
                        onClick={() => {
                          setPortraitPanelHint(null)
                          void regenerateAvatarPortrait()
                        }}
                        type="button"
                      >
                        重新生成
                      </button>
                      <EditAvatarButton
                        busy={avatarBusy || sealingPortrait}
                        disabledByReference={Boolean(refImage || presentationRef)}
                        onEdit={() => {
                          setPortraitPanelHint(null)
                          void editAvatarPortrait()
                        }}
                      />
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        disabled={avatarBusy || sealingPortrait}
                        onClick={() => void reloadAvatarPortrait()}
                        type="button"
                      >
                        重新加载
                      </button>
                    </div>
                    <button
                      className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                      disabled={
                        !portraitUrl ||
                        portraitPreviewId == null ||
                        portraitPreviewId !== activeAvatarId ||
                        sealingPortrait
                      }
                      onClick={() => void sealPortrait()}
                      type="button"
                    >
                      确认
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {phase === 'fullbody-reference' && activeAvatarId != null && (
            <FullbodyReferencePanel
              avatarId={activeAvatarId}
              key={activeAvatarId}
              onBack={onBack}
              onContinue={confirmFullbody}
            />
          )}

          {phase === 'voice' && voiceStage === 'catalog' && voiceLoad === 'ready' && voice && (
            <div className="mt-1">
              <p className="mb-3 text-[13px] text-body">挑一个我说话的声音吧，随时可以试听。</p>
              <div className="rounded-xl border border-line-hairline bg-surface-card p-3">
                <p className="mb-1 text-[10px] tracking-wide text-muted">为你推荐</p>
                <div className="flex items-start justify-between gap-3 text-xs text-strong">
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 truncate font-medium">
                      {voice.label}
                      <VoiceProviderBadge provider={voice.provider} />
                    </p>
                    {voice.tags.length > 0 && (
                      <p className="mt-0.5 truncate text-[10px] text-muted">{voice.tags.join(' · ')}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-3">
                    <button
                      className="transition hover:text-strong disabled:opacity-40"
                      disabled={voicePreparing}
                      onClick={() => previewVoice(voice, 'onboarding.voice.preview.try')}
                      type="button"
                    >
                      试听
                    </button>
                    <button
                      className="transition hover:text-strong disabled:opacity-40"
                      disabled={voicePreparing}
                      onClick={() => {
                        const next = nextVoice(voiceSelectionId(voice), voiceCandidates)

                        if (next) {
                          selectVoice(next, 'onboarding.voice.preview.next')
                        }
                      }}
                      type="button"
                    >
                      换一个
                    </button>
                  </div>
                </div>
              </div>

              <p className="mt-3 mb-1 text-[10px] tracking-wide text-muted">浏览目录</p>
              <div className="max-h-48 overflow-y-auto rounded-xl border border-line-hairline bg-surface-card">
                {otherVoices.length === 0 ? (
                  <p className="px-3 py-4 text-center text-[10px] text-faint">没有更多音色可选</p>
                ) : (
                  otherVoices.map(v => (
                    <button
                      className="flex w-full items-center justify-between gap-3 border-b border-line-hairline px-3 py-2 text-left text-xs text-body transition last:border-b-0 hover:bg-fill-hover disabled:opacity-40"
                      disabled={voicePreparing}
                      key={voiceSelectionId(v)}
                      onClick={() => selectVoice(v, 'onboarding.voice.preview.try')}
                      type="button"
                    >
                      <span className="min-w-0">
                        <span className="flex items-center gap-1.5 truncate">
                          {v.label}
                          <VoiceProviderBadge provider={v.provider} />
                        </span>
                        {v.tags.length > 0 && (
                          <span className="block truncate text-[10px] text-faint">{v.tags.join(' · ')}</span>
                        )}
                      </span>
                      <span className="shrink-0 text-[10px] text-faint">试听并选择</span>
                    </button>
                  ))
                )}
              </div>
              <p className="mt-1 text-[10px] text-muted">
                {voiceCatalog.length} 个音色 · 先挑个差不多的就行，以后随时能在设置里调。
              </p>
              <div className="mt-3 flex items-center justify-between gap-3 text-xs">
                <button
                  className="text-body transition hover:text-strong"
                  onClick={() => setVoiceStage('describe')}
                  type="button"
                >
                  上一步
                </button>
                <button
                  className="h-9 flex-1 rounded-lg bg-accent text-sm font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                  onClick={confirmVoice}
                  type="button"
                >
                  使用这个
                </button>
              </div>
            </div>
          )}

          {phase === 'voice' && voiceStage === 'catalog' && (voiceLoad !== 'ready' || !voice) && (
            <div className="mt-1">
              {voiceLoad === 'loading' ? (
                <SpinnerWithText text="正在加载音色…" />
              ) : (
                <p className="text-[13px] text-body">
                  {voiceLoad === 'failed'
                    ? '音色加载失败，请检查网络后重试。'
                    : '当前系统语言没有可用音色，请先配置支持该语言的 TTS 供应商。'}
                </p>
              )}
              <div className="mt-3 flex items-center gap-4 text-xs">
                <button
                  className="text-body transition hover:text-strong"
                  onClick={() => setVoiceStage('describe')}
                  type="button"
                >
                  上一步
                </button>
                {voiceLoad === 'failed' && (
                  <button
                    className="text-strong transition hover:text-accent"
                    onClick={() => setVoiceLoadAttempt(n => n + 1)}
                    type="button"
                  >
                    重试
                  </button>
                )}
              </div>
            </div>
          )}

          {phase === 'finishing' && <p className="py-6 text-center text-sm text-strong">正在保存资料…</p>}
        </div>
      </div>
    </div>
  )
}

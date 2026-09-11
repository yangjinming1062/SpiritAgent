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
  $voicePreparing,
  APPEARANCE_PRESETS,
  applyPortrait,
  assembleCharacterPersona,
  assemblePersona,
  CHARACTER_GENDER_PRESETS,
  clearDraftRefImage,
  clearPortraitHistory,
  fetchVoiceCatalogRaw,
  hydratePortraitHistory,
  loadDraftRefImage,
  matchVoicePreference,
  MAX_APPEARANCE,
  MAX_USER_TEXT,
  nextVoice,
  type OnboardingAnswers,
  PERSONALITY_PRESETS,
  pickAvatarImage,
  type PickedImage,
  type PortraitEntry,
  pushPortraitEntry,
  RELATIONSHIP_PRESETS,
  resolvePortraitUrl,
  sampleLine,
  saveDraftRefImage,
  selectAvatar,
  selectPortraitEntry,
  setCompanionVoiceId,
  SPEAKING_STYLE_PRESETS,
  speakScripted,
  SPECIES_PRESETS,
  stopSpeaking,
  useInteractiveRegion,
  USER_AGE_BUCKET_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS,
  type VoiceOption,
  VoiceProviderBadge,
  voiceSelectionId,
  warmAudioContext
} from '@/companion'
import { useGatewayRequest } from '@/shared'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { FolderOpen, Sparkles } from '@/shared/lib/icons'
import { isClientErrorIpc, unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { cn } from '@/shared/lib/utils'
import { INPUT_CLASS } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'

import { computeBackTransition } from './back-transition'
import { type OnboardingAudioTag, playOnboardingAudio } from './onboarding-audio'
import {
  Chip,
  HistoryGallery,
  type HistoryGalleryItem,
  PortraitLightbox,
  PortraitPanel,
  useNaturalAspectRatio
} from './onboarding-components'
import { useRegeneratePortrait } from './use-regenerate-portrait'

type Phase =
  | 'q-character'
  | 'portrait-choose'
  | 'hatching'
  | 'portrait-avatar'
  | 'fullbody'
  | 'q-user'
  | 'voice'
  | 'finishing'
  | 'greeting'

type VoiceStage = 'describe' | 'catalog'

type QKey = keyof OnboardingAnswers

// 这个 chip 用来挑「用户接下来要填的是哪一类答案」，本身不是答案——见 CALL_NAME_KINDS。
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
  // Manifest tag 与录到的语音行绑定，而不是与位置绑定——重排 QUESTIONS 也不能让音频错位。
  audioTag: OnboardingAudioTag
  presets?: readonly string[]
  max?: number
  // 允许用户在文本答案之外再附带一张参考图。
  allowImage?: boolean
  // 与 `presets` 互斥：双层入口，而不是「点 chip 就把输入框填好」。
  kinds?: readonly AnswerKind[]
}

// "名字 / 昵称" 是称呼的类别，不是称呼本身——把 chip 的字面文本写进输入框会
// 把「昵称」存成怎么称呼用户。点 chip 只是给输入框换个标签，再去问具体的值；
// 「称号」再额外给几个现成选项，因为它本身就是答案。
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
    key: 'species',
    text: '那我是哪种生灵呢？',
    placeholder: '如：精灵、人类、龙…（可直接输入或选择标签）',
    required: true,
    multiline: false,
    audioTag: 'onboarding.q1',
    presets: SPECIES_PRESETS
  },
  {
    key: 'character_gender',
    text: '嗯…那我是男性、女性、还是…',
    placeholder: '或者自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q2',
    presets: CHARACTER_GENDER_PRESETS
  },
  {
    // appearance：外貌特征——驱动 3D 模型 prompt 与角色外貌描述。
    key: 'appearance',
    text: '您希望我长什么样？说说头发、眼睛、体型、标志性细节…',
    placeholder: '比如：金发绿眼、额间一道疤、机械义眼…',
    required: false,
    multiline: true,
    audioTag: 'onboarding.q3',
    max: MAX_APPEARANCE,
    presets: APPEARANCE_PRESETS,
    allowImage: true
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
  // speaking_style 是后端 schema 的必填项——用专门一道题去问，
  // 让用户的选择成为直接真相来源；它属于角色字段，跟其它字段一起进 enterHatching 的 PUT。
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
    key: 'user_age_bucket',
    text: '您属于哪个年龄段？',
    placeholder: '或自由描述…',
    required: false,
    multiline: false,
    audioTag: 'onboarding.q8',
    max: MAX_USER_TEXT,
    presets: USER_AGE_BUCKET_PRESETS
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

// 这些字段的值会驱动 3D 模型，因此用户在确认头像后不能再改。
// 题面旁边会渲染一个红色 `*`，向导顶部还有 banner 提示用户这一限制。
const LOCKED_FIELD_KEYS: ReadonlySet<QKey> = new Set(['species', 'character_gender', 'appearance'])

// 锁定字段 → 字段名（DESIGN §5.4 横幅按字段聚焦提示）。
const LOCKED_FIELD_LABELS: Partial<Record<QKey, string>> = {
  species: '物种',
  character_gender: '性别',
  appearance: '基础外貌'
}

// 分段边界由 ``voice`` 这道题的位置决定——之前全是角色子阶段，
// 它本身是声音子阶段，之后都是用户子阶段。对应后端 ONBOARDING_FIELDS 的顺序。
const VOICE_Q_INDEX = QUESTIONS.findIndex(q => q.key === 'voice')
const CHARACTER_QUESTIONS: readonly Question[] = QUESTIONS.slice(0, VOICE_Q_INDEX)
const VOICE_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX, VOICE_Q_INDEX + 1)
const USER_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX + 1)

const PHASE_QUESTIONS: Record<Phase, readonly Question[]> = {
  'q-character': CHARACTER_QUESTIONS,
  'q-user': USER_QUESTIONS,
  voice: VOICE_QUESTIONS,
  'portrait-choose': [],
  hatching: [],
  'portrait-avatar': [],
  fullbody: [],
  finishing: [],
  greeting: []
}

// 把 resume 的 next_field 路由到 q-user；`voice` 有自己的分支。
// 从 USER_QUESTIONS 推导，题目增删时自动保持同步。
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

// 可经 onboarding.submit 提交的 question key——与后端 ONBOARDING_FIELDS 对齐。
// 映射都是恒等的（question key === 后端字段名），所以用 Set 就够了。
const ONBOARDING_FIELD_KEYS: ReadonlySet<QKey> = new Set<QKey>([
  'name',
  'species',
  'character_gender',
  'appearance',
  'relationship',
  'personality',
  'speaking_style',
  'user_call_name',
  'user_gender',
  'user_age_bucket',
  'user_hobbies',
  'user_freeform',
  'voice'
])

// 头像（半身像）生成。返回后端的原始响应；解析步骤由 applyPortrait 负责。
async function generatePortrait(reference: PickedImage | null): Promise<{
  asset_url?: string
  id?: number
} | null> {
  try {
    const res = await window.spiritagent.api<{
      asset_url?: string
      id?: number
    }>({
      path: reference ? '/api/companion/avatar/from-image' : '/api/companion/avatar',
      method: 'POST',
      body: reference ? { content_type: reference.contentType, image: reference.base64 } : {}
    })

    return res
  } catch (error) {
    // 把确定性的失败重抛出去，避免 retryTransient 烧掉 120 秒的头像预算。
    if (isClientErrorIpc(error)) {
      throw error
    }

    return null
  }
}

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

    return false
  }
}

interface OnboardingFlowProps {
  onCompleted: () => void
}

// 放到 OnboardingFlow 外面：否则它订阅的 $regenFeedback 会在每次按键时让整个对话框重渲染。
function RegenFeedbackInput(): React.JSX.Element {
  const value = useStore($regenFeedback)

  return (
    <textarea
      className={`${INPUT_CLASS} text-xs`}
      maxLength={MAX_APPEARANCE}
      onChange={e => $regenFeedback.set(e.target.value)}
      placeholder="哪里不满意？比如：头发再短一点、眼睛再大一点、表情更温和…（可留空直接重新生成）"
      rows={2}
      value={value}
    />
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
  const { requestGateway } = useGatewayRequest()
  const [phase, setPhase] = useState<Phase>('q-character')
  // confirm-front 成功后置 true:形象已锁死 + 3D 已启动,任何返回到 portrait-avatar / fullbody 的路径都禁用
  const [imageSealed, setImageSealed] = useState(false)
  const [qIndex, setQIndex] = useState(0)
  const onboardingSubmissionsRef = useRef(Promise.resolve())
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  // 当前头像行 id 由 applyPortrait 写入全局 $activeAvatarId atom——
  // 这里订阅它，让重生结果自动传播，省得我们在每个调用点都手写 setState。
  const activeAvatarId = useStore($activeAvatarId)
  // 历史画廊——头像面板下方的缩略图。
  const portraitHistory = useStore($portraitHistory)
  const portraitSelectedIdx = useStore($portraitSelectedIdx)
  // voice 阶段先跑 Q7 描述输入，再进入目录选择器。
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('describe')

  // 失败时保留当前头像：它已经持有解析好的字节。
  // 共用的 `applyPortrait` 负责写入全局 $portraitUrl 与 $activeAvatarId atom。
  const applyLocalPortrait = async (
    response:
      | {
          asset_url?: string | null
          id?: number
        }
      | null
      | undefined
  ): Promise<{ assetUrl: string | null; avatar: string | null; id: number | null }> => {
    const { avatar } = await applyPortrait({
      id: response?.id,
      assetUrl: response?.asset_url
    })

    if (avatar) {
      setPortraitUrl(avatar)
    }

    return { assetUrl: response?.asset_url ?? null, avatar, id: response?.id ?? null }
  }

  const [voice, setVoice] = useState<VoiceOption | null>(null)
  const [voiceCatalog, setVoiceCatalog] = useState<VoiceOption[]>([])
  // 匹配器的候选项。跟完整目录分开，方便「推荐卡」的「换一个」按钮在候选项里循环，
  // 而不是遍历整个目录。
  const [voiceAlternatives, setVoiceAlternatives] = useState<VoiceOption[]>([])
  // 失败提示挂在头像面板上——表单区被它压在下面。
  const [portraitPanelHint, setPortraitPanelHint] = useState<string | null>(null)
  const [avatarUploading, setAvatarUploading] = useState(false)

  const [fullbodyLoading, setFullbodyLoading] = useState(false)
  const [fullbodyLoadingText, setFullbodyLoadingText] = useState('正在为您生成正面全身立绘…')
  const [fullbodyStyle, setFullbodyStyleState] = useState<string | null>('cel_shading')
  const [fullbodyFrontUrl, setFullbodyFrontUrl] = useState<string | null>(null)
  const [fullbodyFrontRawUrl, setFullbodyFrontRawUrl] = useState<string | null>(null)
  const [fullbodyFeedback, setFullbodyFeedback] = useState<string>('')
  const [fullbodyHint, setFullbodyHint] = useState<string | null>(null)
  const [fullbodyZoomUrl, setFullbodyZoomUrl] = useState<string | null>(null)

  const [fullbodyHistories, setFullbodyHistories] = useState<
    Record<string, Array<{ rawUrl: string | null; previewUrl: string }>>
  >({})

  const [fullbodyHistoryIndices, setFullbodyHistoryIndices] = useState<Record<string, number>>({})

  // 全身画幅随物种骨骼分桶（竖/方/横并存）：确认卡取景框比例跟随当前立绘图片本身
  const fullbodyRatio = useNaturalAspectRatio(fullbodyFrontUrl)

  // 在「形象描述」题目提交上来的参考图。本地用 IndexedDB 草稿缓存持久化，
  // 这样半身生成前崩溃后重启也能带回来。
  const [refImage, setRefImage] = useState<PickedImage | null>(null)

  // 头像重生时挑的展现/画风参考图——跟 Q4 的身份图共存，而不是替换它。
  // 仅存内存：是临时性的重生辅助，不是持久化的身份资产。
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
  const containerRef = useRef<HTMLDivElement>(null)

  // 居中的初始位置；用户可以从这里开始拖拽。
  const [dialogPos, setDialogPos] = useState<{ x: number; y: number }>(() => {
    const width = 448
    const height = 600

    return {
      x: Math.max(0, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(0, Math.round((window.innerHeight - height) / 2))
    }
  })

  // Onboarding 对话框完全可交互——把它的实际可见矩形注册到全局 interactive-regions 注册表，
  // SpriteStage 的命中测试就只在光标停在对话框表单卡片上时才捕获。卸载时 SpriteStage 会恢复穿透。
  useInteractiveRegion('onboarding', containerRef, interactiveRegionRect)

  useEffect(() => {
    return () => {
      stopSpeaking()
    }
  }, [])

  // q1 之前预热 ctx，避免 MediaElementSource 重路由吃掉首帧。
  useEffect(() => {
    warmAudioContext()
  }, [])

  // 反馈 textarea 在多个头像面板之间共享。阶段切换时清空，
  // 避免 onboarding 里输入的「头发再短一点」泄漏到后续重生流程里。
  useEffect(() => {
    if (phase === 'portrait-avatar') {
      $regenFeedback.set('')
    }
  }, [phase])

  // 拖拽用 document 级监听器（而不是容器上的 React onPointerMove），
  // 这样光标离开对话框矩形后拖拽仍能继续。阈值过滤点击；表单控件由下面的 wrapper 屏蔽。
  // 基准位与实时位移分离：dialogPos 只在松手时提交，拖拽中用 hook 内部 delta 做视觉偏移，
  // 避免把「相对 pointerdown 的累计位移」叠到不断被重设的 origin 上导致加速漂移。
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
  // 用 ref 持有最新 answers，让 speak/focus effect 只在 phase/qIndex 变化时重跑，
  // 而不是每次按键都跑（exhaustive-deps lint 看不到这个意图）。
  const answersRef = useLatestRef(answers)

  // 题面文本，显示在输入框下方。
  const spokenText = question?.text ?? ''

  // 每道题出现时朗读（DESIGN §5.2 预制语音）。
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
    setInput(initialVal)
    setAnswerKind(null)
    setHint(null)

    void playOnboardingAudio(q.audioTag)

    return () => stopSpeaking()
  }, [phase, qIndex, currentList, answersRef])

  const submitOnboardingAnswer = useCallback(
    (field: QKey, value: string | null) => {
      const submission = onboardingSubmissionsRef.current
        .then(async () => {
          await requestGateway('onboarding.submit', { field, value })
        })
        .catch(() => undefined)

      onboardingSubmissionsRef.current = submission

      return submission
    },
    [requestGateway]
  )

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

    // 逐字段增量持久化（DESIGN §5.2 断点恢复）；fire-and-forget——绝不阻塞 UI 等草稿保存。
    // 网关未打开前是空操作。
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

  // 在 describe→catalog 切换（以及 resume 直接进入 catalog）时加载目录与试听 TTS。
  useEffect(() => {
    if (phase !== 'voice' || voiceStage !== 'catalog') {
      return
    }
    void (async () => {
      stopSpeaking()

      const [matched, result] = await Promise.all([
        matchVoicePreference(requestGateway, answers.voice ?? ''),
        fetchVoiceCatalogRaw(requestGateway)
      ])

      const catalog = result.ok ? result.catalog.voices : []
      const selected = matched.voice ?? catalog[0] ?? null
      setVoice(selected)
      setVoiceAlternatives(matched.alternatives)
      setCompanionVoiceId(selected ? voiceSelectionId(selected) : '')
      // 同时把 matched voice 与它的 alternatives 都剔除——它们已经被前置了。
      // 不剔除的话，同时也在目录里的 alternative（如「茉莉」）会重复出现，
      // 每次切换声音时列表都能看出重了。
      const priorityVoices = selected ? [selected, ...matched.alternatives] : []
      const priorityIds = new Set(priorityVoices.map(voiceSelectionId))
      const extra = catalog.filter(v => !priorityIds.has(voiceSelectionId(v)))
      setVoiceCatalog([...priorityVoices, ...extra])

      if (!selected) {
        return
      }

      void speakScripted(sampleLine(answers.name || ''), voiceSelectionId(selected), 'onboarding.voice.preview')
    })()
  }, [phase, voiceStage, requestGateway, answers.voice, answers.name])

  const onSend = (): void => {
    const q = currentList[qIndex]

    if (q?.required && !input.trim()) {
      const requiredHints: Record<string, string> = {
        name: '名字是必填的哦～',
        species: '生灵类型是必填的哦～',
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
    // 形象确认后 3D 已启动,任何返回路径都禁用——纯函数 ``computeBackTransition`` 在 imageSealed 时直接返 null。
    const intent = computeBackTransition({ phase, qIndex, voiceStage, imageSealed }, CHARACTER_QUESTIONS.length)

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

    // 先固化 persona 再进入头像阶段——让用户自主选择「AI 生成」或「直接上传」。
    let personaOk = false
    await onboardingSubmissionsRef.current

    try {
      personaOk = (await retryTransient(() => savePersona(assembleCharacterPersona(ans)), 700)) === true
    } catch (err) {
      setPhase('q-character')
      setHint(err instanceof Error ? `记忆存不上：${err.message}` : '记忆存不上，请重试 onboarding')
      void playOnboardingAudio('onboarding.hatching.retry')

      return
    }

    if (!personaOk) {
      setHint('记忆还没存好，稍后再试试形象吧…')

      return
    }

    setPhase('portrait-choose')
  }

  const startAiHatching = async (imageOverride?: PickedImage | null): Promise<void> => {
    if (imageSealed) {
      return
    }

    const img = imageOverride !== undefined ? imageOverride : refImage
    setPhase('hatching')
    setHint(null)
    void playOnboardingAudio('onboarding.hatching')

    let url: string | null = null

    try {
      // DESIGN §5.3：avatar 生成最多 3 次（1 次初试 + 2 次重试），不暴露技术错误。
      const applied = await applyLocalPortrait(await retryTransient(() => generatePortrait(img), 1500, 3))
      url = applied.avatar

      if (url) {
        pushPortraitEntry({
          assetUrl: applied.assetUrl,
          avatarId: applied.id ?? activeAvatarId,
          portraitUrl: url
        })
      }
    } catch {
      // 确定性的 4xx（参考图不可用、persona 不完整）不能让流程卡在 'hatching'——
      // 直接落到 portrait 阶段，那里仍然支持带反馈或不带反馈的重生。
      url = null
    }

    if (!url) {
      // 接下来渲染的是 portrait 面板；`hint` 只在表单里能看到。
      setPortraitPanelHint(img ? '这张参考图我没能用上…待会儿再换一张吧' : '我还没想好…')
    }

    // avatar 阶段：用户审视头像并确认——确认即锁定形象，并解锁 voice 子阶段。
    setPhase('portrait-avatar')
    void playOnboardingAudio(url ? 'onboarding.portrait.ok' : 'onboarding.portrait.failed')
  }

  const generateFullbodyFrontDirect = async (avatarId: number, styleId = 'cel_shading'): Promise<void> => {
    setFullbodyLoading(true)
    setFullbodyLoadingText('正在为您生成正面全身立绘…')
    setFullbodyHint(null)
    setFullbodyStyleState(styleId)

    const effectiveRef = refImage ?? presentationRef

    try {
      const res = await window.spiritagent.api<{
        id?: number
        asset_url?: string
        seed_front_2d_url?: string
      }>({
        path: `/api/companion/avatar/${avatarId}/fullbody/front-2d`,
        method: 'POST',
        body: {
          style: styleId,
          image: effectiveRef?.base64,
          content_type: effectiveRef?.contentType
        }
      })

      const applied = await applyPortrait({
        id: res?.id,
        assetUrl: res?.asset_url,
        seedFrontUrl: res?.seed_front_2d_url
      })

      const rawFront = res?.seed_front_2d_url || null
      let resolvedUrl: string | null = null

      if (applied.seedFront) {
        resolvedUrl = applied.seedFront
      } else if (rawFront) {
        resolvedUrl = await resolvePortraitUrl(rawFront)
      }

      if (resolvedUrl) {
        setFullbodyFrontRawUrl(rawFront)
        setFullbodyFrontUrl(resolvedUrl)
        setFullbodyHistories({ [styleId]: [{ rawUrl: rawFront, previewUrl: resolvedUrl }] })
        setFullbodyHistoryIndices({ [styleId]: 0 })
      } else {
        setFullbodyHint('正面立绘加载失败，请重试')
      }
    } catch (err) {
      setFullbodyHint(err instanceof Error ? err.message : '生成正面全身立绘失败，请重试')
    } finally {
      setFullbodyLoading(false)
    }
  }

  // 从 avatar 行复水 fullbody 阶段——正面种子图——
  // 让重启能从上次中断处继续，且绝不重新触发生成。
  // 仅在没有存储内容（首次进入 / 旧版行）或 temp-media 草稿过了 TTL 时才回退到重新生成。
  const hydrateFullbodyStage = async (): Promise<void> => {
    const avatarRes = await window.spiritagent.api<{
      asset_url?: string | null
      seed_front_2d_url?: string | null
      id?: number
      fullbody_style?: string | null
    }>({
      path: '/api/companion/avatar',
      method: 'GET'
    })

    await applyLocalPortrait(avatarRes)

    const style = 'cel_shading'
    const seedFrontRaw = avatarRes?.seed_front_2d_url || null

    if (seedFrontRaw) {
      const resolved = await resolvePortraitUrl(seedFrontRaw)
      setFullbodyStyleState(style)
      setFullbodyFrontRawUrl(seedFrontRaw)
      setFullbodyFrontUrl(resolved)

      if (resolved) {
        setFullbodyHistories({ [style]: [{ rawUrl: seedFrontRaw, previewUrl: resolved }] })
        setFullbodyHistoryIndices({ [style]: 0 })
      }
    } else if (avatarRes?.id) {
      await generateFullbodyFrontDirect(avatarRes.id, style)
    } else {
      setFullbodyStyleState(style)
      setFullbodyFrontRawUrl(null)
      setFullbodyFrontUrl(null)
      setFullbodyHistories({})
      setFullbodyHistoryIndices({})
    }
  }

  const hydrateFullbodyStageRef = useLatestRef(hydrateFullbodyStage)

  // 断点恢复（DESIGN §5.2）：网关一旦连通，
  // 就把还没答完的草稿拉回来，让 onboarding 中途崩溃/退出后能从下一道未答的题继续。
  // 只跑一次，绝不重复 resume。
  useEffect(() => {
    if (resumedRef.current || gatewayState !== 'open') {
      return
    }

    resumedRef.current = true

    void (async () => {
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
        } catch {
          state = await requestGateway<{
            answers?: Record<string, string>
            next_field?: string | null
            complete?: boolean
          }>('onboarding.get_state', {}).catch(() => null)
        }

        if (state?.complete) {
          void clearDraftRefImage()
          onCompleted()

          return
        }

        if (state?.answers) {
          // 把服务端草稿与当前会话里已经输入的答案合并；
          // 本地非空的编辑优先，保证用户最近的意图不会丢失。
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
            } catch {
              setPhase('portrait-choose')
            }
          } else if (nextField === 'fullbody') {
            try {
              // portrait 确认后 persona 已定稿;resume 直接落到 fullbody 阶段。
              setPhase('fullbody')
              await hydratePortraitHistory()
              await hydrateFullbodyStageRef.current()
            } catch {
              setPhase('fullbody')
              setFullbodyHint('全身立绘恢复失败，请重试')
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
      } catch {
        /* no draft yet — start fresh */
      }

      const r = await fetchVoiceCatalogRaw(requestGateway)

      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }
    })()
  }, [gatewayState, requestGateway, onCompleted, hydrateFullbodyStageRef])

  useEffect(() => {
    if (gatewayState !== 'open' || voiceCatalog.length > 0) {
      return
    }

    void fetchVoiceCatalogRaw(requestGateway).then(r => {
      if (r.ok) {
        setVoiceCatalog(r.catalog.voices)
      }
    })
  }, [gatewayState, requestGateway, voiceCatalog.length])

  // 第一步——头像重生：新建一行 avatar，新 id 通过 hook 内 applyPortrait 自动发布到 ``$activeAvatarId``。
  const { regenerate: regenerateAvatarPortrait, busy: avatarBusy } = useRegeneratePortrait({
    refImage,
    presentationRef,
    playAudioOnSuccess: true,
    onRegenerated: ({ avatar }) => {
      if (avatar) {
        setPortraitUrl(avatar)
      }
    }
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
        $portraitUrl.set(entry.portraitUrl)
      }

      // 用户在画廊里点选时：当前 avatar 行必须跟显示的脸保持一致，
      // 否则选中会悄悄回退到最后一行，画面跳回用户已经拒绝过的那张脸。
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

  const uploadCustomAvatar = async (): Promise<void> => {
    const picked = await pickAvatarImage('选择一张头像图片')

    if (!picked) {
      return
    }

    if ('error' in picked) {
      setPortraitPanelHint(picked.error)

      return
    }

    setAvatarUploading(true)
    setPortraitPanelHint(null)

    try {
      const res = await window.spiritagent.api<{
        asset_url?: string
        id?: number
      }>({
        body: {
          content_type: picked.image.contentType,
          image: picked.image.base64
        },
        method: 'POST',
        path: '/api/companion/avatar/upload'
      })

      const applied = await applyLocalPortrait(res)

      if (applied.avatar) {
        pushPortraitEntry({
          assetUrl: applied.assetUrl,
          avatarId: applied.id ?? activeAvatarId,
          portraitUrl: applied.avatar
        })
        $regenFeedback.set('')
        setPhase('portrait-avatar')
        void playOnboardingAudio('onboarding.portrait.ok')
      }
    } catch (err) {
      setPortraitPanelHint(err instanceof Error ? err.message : '上传头像失败，请稍后重试')
    } finally {
      setAvatarUploading(false)
    }
  }

  const pickPresentationImage = async (): Promise<void> => {
    const picked = await pickAvatarImage('选择一张风格参考图')

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

  const confirmPortrait = async (): Promise<void> => {
    try {
      await window.spiritagent.api({
        path: '/api/companion/portrait/confirm',
        method: 'POST'
      })
    } catch (error) {
      // 409 表示 temp-media 已过期——头像文件已不在，绝不能继续推进。
      // 退回 avatar 阶段，让用户重新生成。
      if (isClientErrorIpc(error)) {
        const unwrapped = unwrapIpcErrorMessage(error)
        const jsonStart = unwrapped.indexOf('{')
        const parsed = jsonStart >= 0 ? safeJsonParse(unwrapped.slice(jsonStart), null) : null
        const backendError = (parsed as { detail?: { error?: string } } | null)?.detail?.error

        if (backendError) {
          setPortraitPanelHint(backendError)
          setPhase('portrait-avatar')

          return
        }
      }
      // 非 IPC 失败（网络、JSON 解析、IPC envelope）：onClick 里的 `void` 会把异常吞掉——
      // 这里显式提示并拒绝推进。

      console.warn('confirmPortrait failed unexpectedly', error)
      setPortraitPanelHint('确认失败，请检查网络后重试')

      return
    }

    clearPortraitHistory()
    setPresentationRef(null)

    // portrait 确认后直接进入全身立绘阶段；2D 形象默认赛璐珞风格，自动生成正面立绘。
    setPhase('fullbody')
    setFullbodyStyleState('cel_shading')
    setFullbodyFrontUrl(null)
    setFullbodyFrontRawUrl(null)
    setFullbodyFeedback('')
    setFullbodyHint(null)
    setFullbodyZoomUrl(null)
    setFullbodyHistories({})
    setFullbodyHistoryIndices({})

    if (activeAvatarId) {
      void generateFullbodyFrontDirect(activeAvatarId, 'cel_shading')
    }
  }

  const onSelectFullbodyHistoryEntry = useCallback(
    (idx: number) => {
      if (!fullbodyStyle) {
        return
      }

      const list = fullbodyHistories[fullbodyStyle] || []

      if (idx >= 0 && idx < list.length) {
        const entry = list[idx]
        setFullbodyHistoryIndices(prev => ({ ...prev, [fullbodyStyle]: idx }))
        setFullbodyFrontUrl(entry.previewUrl)
        setFullbodyFrontRawUrl(entry.rawUrl)
      }
    },
    [fullbodyHistories, fullbodyStyle]
  )

  const regenerateFullbodyFront = async (): Promise<void> => {
    if (!activeAvatarId || !fullbodyStyle || fullbodyLoading) {
      return
    }

    setFullbodyLoading(true)
    setFullbodyLoadingText('正在按要求重新生成正面全身图…')
    setFullbodyHint(null)

    const effectiveRef = refImage ?? presentationRef

    try {
      const res = await window.spiritagent.api<{
        id?: number
        asset_url?: string
        seed_front_2d_url?: string
      }>({
        path: `/api/companion/avatar/${activeAvatarId}/fullbody/front-2d`,
        method: 'POST',
        body: {
          style: fullbodyStyle,
          feedback: fullbodyFeedback.trim() || undefined,
          image: effectiveRef?.base64,
          content_type: effectiveRef?.contentType
        }
      })

      const applied = await applyPortrait({
        id: res?.id,
        assetUrl: res?.asset_url,
        seedFrontUrl: res?.seed_front_2d_url
      })

      const rawFront = res?.seed_front_2d_url || null
      let resolvedUrl: string | null = null

      if (applied.seedFront) {
        resolvedUrl = applied.seedFront
      } else if (rawFront) {
        resolvedUrl = await resolvePortraitUrl(rawFront)
      }

      if (resolvedUrl) {
        setFullbodyFrontRawUrl(rawFront)
        setFullbodyFrontUrl(resolvedUrl)

        let targetIdx = 0
        setFullbodyHistories(prev => {
          let currentList = prev[fullbodyStyle] || []

          if (currentList.length === 0 && fullbodyFrontUrl) {
            currentList = [{ rawUrl: fullbodyFrontRawUrl, previewUrl: fullbodyFrontUrl }]
          }

          const nextList = [...currentList, { rawUrl: rawFront, previewUrl: resolvedUrl }]

          if (nextList.length > 5) {
            nextList.shift()
          }

          targetIdx = nextList.length - 1

          return { ...prev, [fullbodyStyle]: nextList }
        })

        setFullbodyHistoryIndices(prev => ({ ...prev, [fullbodyStyle]: targetIdx }))
      }
    } catch (err) {
      setFullbodyHint(err instanceof Error ? err.message : '重新生成正面全身图失败，请重试')
    } finally {
      setFullbodyLoading(false)
    }
  }

  const confirmFullbodyFront = async (): Promise<void> => {
    if (!activeAvatarId || !fullbodyStyle || fullbodyLoading) {
      return
    }

    const avatarId = activeAvatarId
    const style = fullbodyStyle
    const frontUrl = fullbodyFrontRawUrl

    setFullbodyLoading(true)
    setFullbodyLoadingText('正在确认全身形象…')
    setFullbodyHint(null)

    try {
      const res = await window.spiritagent.api<{
        id?: number
        asset_url?: string
      }>({
        path: `/api/companion/avatar/${avatarId}/fullbody/confirm-front`,
        method: 'POST',
        body: {
          style,
          front_url: frontUrl || undefined
        }
      })

      await applyPortrait({
        id: res?.id,
        assetUrl: res?.asset_url
      })
      // 形象确认后立即锁死 onBack 路径(返回到 voice → q-character → portrait-avatar → fullbody 会让
      // 用户重新调整正面视图,与已启动的 2D 生成不一致)。
      setImageSealed(true)
      // 形象确认后立即异步启动 2D 骨骼切分,不等 onboarding 剩余步骤(语音/性格) 完成;
      // 生成在 web 进程内 fire-and-forget,失败静默——用户在客户端随时可重试。
      // 渲染模式固定为 2D：onboarding 阶段不暴露模式选择,需要 3D 时可在「伙伴设置 → 渲染模式」切换
      // （切 3D 前会先补生成背面种子图）。
      void window.spiritagent
        .api<{ id?: number; status?: string }>({ path: '/api/companion/2d', method: 'POST', body: {} })
        .catch(() => undefined)

      // 后端确认成功后才能推进——失败时停在当前步,保留按钮可重试。
      setPhase('voice')
      setVoiceStage('describe')
      setQIndex(0)
      setInput('')
      setAnswerKind(null)
      setHint(null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '确认失败，请稍后重试'
      setFullbodyHint(msg)
    } finally {
      setFullbodyLoading(false)
    }
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

    // 兜底重试；失败时退回 'q-user'，避免 phase 卡在 'finishing'。
    try {
      if (voice) {
        await submitOnboardingAnswer('voice', voice.label || voice.id)
      }

      await onboardingSubmissionsRef.current

      await savePersona(assemblePersona(ans))
    } catch (err) {
      setPhase('q-user')
      setQIndex(USER_QUESTIONS.length - 1)
      setHint(err instanceof Error ? `同步失败：${err.message}` : '同步失败，请稍后再试')
      void playOnboardingAudio('onboarding.finishing.retry')

      return
    }

    void clearDraftRefImage()
    updateRefImage(null)
    setPhase('greeting')

    // DESIGN §5.7：首句问候用确认后的音色说出——TTS（speakScripted 按
    // (音色, 台词) 内容寻址缓存）优先，失败才回退预渲染音频片段。
    const greetingName = ans.name?.trim() || ''
    const greetingText = greetingName ? `你好呀！我是${greetingName}，以后就由我陪你啦。` : '你好呀！以后就由我陪你啦。'

    let ok = await speakScripted(greetingText).catch(() => false)

    if (!ok) {
      ok = await playOnboardingAudio('onboarding.greeting')
    }

    if (!ok) {
      setHint('（声音暂时不可用）')
    }

    await sleep(ok ? 600 : 1800)
    onCompleted()
  }

  const presetValues = question?.presets ?? []
  const otherVoices = voice ? voiceCatalog.filter(v => voiceSelectionId(v) !== voiceSelectionId(voice)) : []
  // 只要还有候选项，「换一个」就在候选项里循环。
  const voiceCandidates = voice ? [voice, ...(voiceAlternatives.length ? voiceAlternatives : otherVoices)] : []

  const currentFullbodyHistory: HistoryGalleryItem[] = useMemo(() => {
    if (!fullbodyStyle) {
      return []
    }

    const list = fullbodyHistories[fullbodyStyle] || []

    return list.map(item => ({ url: item.previewUrl }))
  }, [fullbodyHistories, fullbodyStyle])

  const canGoBack =
    computeBackTransition({ phase, qIndex, voiceStage, imageSealed }, CHARACTER_QUESTIONS.length) !== null

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
        <div
          className="w-full rounded-2xl border border-line-standard bg-surface-panel p-5 text-strong shadow-2xl"
          style={{ pointerEvents: 'auto' }}
        >
          {voicePreparing && <p className="mb-2 text-center text-[10px] text-muted">正在准备声音…</p>}
          {phase === 'q-character' && question && LOCKED_FIELD_KEYS.has(question.key) && (
            <p className="mb-2 rounded-md border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-[10px] leading-relaxed text-amber-200/85">
              「{LOCKED_FIELD_LABELS[question.key] ?? '当前字段'}」是形象确认后无法再次更改的重点内容，请仔细选择。
            </p>
          )}
          {(phase === 'q-character' || phase === 'q-user' || (phase === 'voice' && voiceStage === 'describe')) &&
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
                {question.allowImage && (
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <button
                      className="rounded-full border border-dashed border-line-standard px-3 py-1 text-body transition hover:bg-fill-hover"
                      onClick={() => void pickReferenceImage()}
                      type="button"
                    >
                      {refImage ? '换一张参考图' : '＋ 上传参考图'}
                    </button>
                    {refImage && (
                      <>
                        <img alt="参考图" className="h-9 w-9 rounded-md object-cover" src={refImage.previewUrl} />
                        <span className="text-[10px] text-faint">我会照着它画自己</span>
                        <button
                          className="ml-auto text-muted transition hover:text-strong"
                          onClick={() => updateRefImage(null)}
                          type="button"
                        >
                          移除
                        </button>
                      </>
                    )}
                  </div>
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
                {hint && <p className="mt-2 text-xs text-amber-300/80">{hint}</p>}
                <p className="mt-2 text-right text-[10px] text-faint">
                  {qIndex + 1} / {currentList.length}
                </p>
              </>
            )}

          {phase === 'portrait-choose' && (
            <div>
              <p className="text-[15px] font-medium text-strong">选择头像获取方式</p>
              <p className="mt-1 text-xs text-body">
                形象设定已保存！您可以让 AI 根据设定构思形象，或直接上传现有的头像图片。
              </p>

              <div className="mt-4 flex flex-col gap-3">
                <button
                  className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99]"
                  onClick={() => void startAiHatching()}
                  type="button"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                      <Sparkles className="size-4 text-muted" /> 让 AI 为我生成头像
                    </span>
                    <span className="text-xs text-muted">AI 绘制 →</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                    基于您刚才填写的形象描述与参考图，自动构思并生成半身头像（生成后可微调或重新生成）。
                  </p>
                </button>

                <button
                  className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99] disabled:opacity-40"
                  disabled={avatarUploading}
                  onClick={() => void uploadCustomAvatar()}
                  type="button"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-[14px] font-medium text-strong">
                      <FolderOpen className="size-4 text-muted" /> 直接上传已有头像
                    </span>
                    <span className="text-xs text-muted">本地上传 →</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                    跳过 AI 生成，直接使用您本地准备好的图片作为伙伴头像。
                  </p>
                </button>

                {activeAvatarId != null && portraitUrl && (
                  <button
                    className="rounded-xl border border-line-hairline bg-surface-card p-4 text-left transition hover:border-line-strong hover:bg-fill-hover active:scale-[0.99]"
                    onClick={() => setPhase('portrait-avatar')}
                    type="button"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[14px] font-medium text-strong">继续使用当前头像</span>
                      <span className="text-xs text-muted">已有草稿 →</span>
                    </div>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-body">
                      保留之前生成或上传的头像草稿，直接进入确认与全身立绘阶段。
                    </p>
                  </button>
                )}
              </div>

              {avatarUploading && (
                <div className="mt-3">
                  <SpinnerWithText size="h-5 w-5" text="正在上传头像…" />
                </div>
              )}

              {portraitPanelHint && <p className="mt-3 text-xs text-rose-300/90">{portraitPanelHint}</p>}

              <div className="mt-4 flex items-center justify-between text-xs">
                <button className="text-body transition hover:text-strong" onClick={onBack} type="button">
                  上一步
                </button>
              </div>
            </div>
          )}

          {phase === 'hatching' && <SpinnerWithText size="h-6 w-6" text={hint || '让我想想我该是什么样子…'} />}

          {(phase === 'portrait-avatar' || phase === 'greeting') && (
            <PortraitPanel
              avatarUrl={portraitUrl}
              hint={portraitPanelHint}
              history={currentHistoryItems}
              introHint={phase === 'portrait-avatar' ? '先确认半身头像' : null}
              name={answers.name?.trim() || '伙伴'}
              onSelectEntry={onSelectHistoryEntry}
              selectedIdx={portraitSelectedIdx}
            />
          )}

          {phase === 'portrait-avatar' && (
            <div className="mt-4">
              {avatarBusy ? (
                <SpinnerWithText text="正在重新生成头像…" />
              ) : avatarUploading ? (
                <SpinnerWithText text="正在上传头像…" />
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
                        {presentationRef ? '换风格参考图' : '＋ 风格参考图'}
                      </button>
                      {presentationRef && (
                        <>
                          <img
                            alt="风格参考"
                            className="h-9 w-9 rounded-md object-cover"
                            src={presentationRef.previewUrl}
                          />
                          <span className="text-[10px] text-faint">参考风格/展现形式，不影响角色形象</span>
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
                    <div className="flex gap-3">
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        onClick={onBack}
                        type="button"
                      >
                        上一步
                      </button>
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        disabled={avatarBusy || avatarUploading}
                        onClick={() => void regenerateAvatarPortrait()}
                        type="button"
                      >
                        重新生成
                      </button>
                      <button
                        className="rounded-full border border-line-standard px-3 py-1 text-strong transition hover:bg-fill-hover disabled:opacity-40"
                        disabled={avatarBusy || avatarUploading}
                        onClick={() => void uploadCustomAvatar()}
                        type="button"
                      >
                        自己上传
                      </button>
                    </div>
                    <button
                      className="inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                      disabled={activeAvatarId == null}
                      onClick={() => void confirmPortrait()}
                      type="button"
                    >
                      确认
                    </button>
                  </div>
                </>
              )}
              {portraitPanelHint && <p className="mt-2 text-xs text-rose-300/90">{portraitPanelHint}</p>}
            </div>
          )}

          {phase === 'fullbody' && (
            <div className="mt-2">
              {fullbodyLoading ? (
                <div className="py-8 text-center">
                  <SpinnerWithText size="h-6 w-6" text={fullbodyLoadingText} />
                </div>
              ) : (
                <div>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[14px] font-medium text-strong">确认全身立绘</p>
                      <p className="mt-0.5 text-xs text-body">已为您生成正面立绘，可直接确认或输入要求微调重绘。</p>
                    </div>
                  </div>

                  <div
                    className="relative mx-auto mt-3 flex aspect-[9/16] max-h-[320px] w-auto items-center justify-center overflow-hidden rounded-xl border border-line-hairline bg-fill-trough group"
                    style={fullbodyRatio ? { aspectRatio: fullbodyRatio } : undefined}
                  >
                    {fullbodyFrontUrl ? (
                      <button
                        aria-label="放大查看"
                        className="relative block h-full w-full cursor-zoom-in overflow-hidden border-0 bg-transparent p-0"
                        onClick={() => setFullbodyZoomUrl(fullbodyFrontUrl)}
                        type="button"
                      >
                        <img alt="正面全身立绘" className="h-full w-full object-contain" src={fullbodyFrontUrl} />
                        <div className="absolute top-2 right-2 rounded-full bg-black/60 p-1.5 text-white/80 opacity-0 backdrop-blur-sm transition group-hover:opacity-100 hover:bg-black/80 hover:text-white">
                          <svg
                            className="h-3.5 w-3.5"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={2}
                            viewBox="0 0 24 24"
                          >
                            <path
                              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </div>
                      </button>
                    ) : (
                      <div className="p-4 text-center">
                        <p className="mb-2 text-xs text-muted">暂无预览图</p>
                        {activeAvatarId && (
                          <button
                            className="rounded-full bg-fill-hover px-3 py-1 text-xs text-strong hover:bg-fill-active"
                            onClick={() => void generateFullbodyFrontDirect(activeAvatarId, 'cel_shading')}
                            type="button"
                          >
                            重新生成
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {currentFullbodyHistory.length > 1 && (
                    <div className="mt-2">
                      <HistoryGallery
                        entries={currentFullbodyHistory}
                        onSelect={onSelectFullbodyHistoryEntry}
                        selectedIdx={fullbodyHistoryIndices['cel_shading'] ?? currentFullbodyHistory.length - 1}
                      />
                    </div>
                  )}

                  <div className="mt-3">
                    <textarea
                      className={`${INPUT_CLASS} text-xs`}
                      maxLength={MAX_APPEARANCE}
                      onChange={e => setFullbodyFeedback(e.target.value)}
                      placeholder="对正面立绘有微调要求？例如：头发再长一点、换个服饰配色…（可留空直接确认）"
                      rows={2}
                      value={fullbodyFeedback}
                    />
                  </div>

                  {fullbodyHint && <p className="mt-2 text-xs text-rose-300/90">{fullbodyHint}</p>}

                  <div className="mt-3 flex items-center justify-between text-xs">
                    <button
                      className="text-body transition hover:text-strong"
                      onClick={() => setPhase('portrait-avatar')}
                      type="button"
                    >
                      上一步
                    </button>
                    <div className="flex gap-3">
                      <button
                        className="text-body transition hover:text-strong disabled:opacity-40"
                        disabled={fullbodyLoading}
                        onClick={() => void regenerateFullbodyFront()}
                        type="button"
                      >
                        微调重绘
                      </button>
                      <button
                        className="inline-flex h-9 items-center justify-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
                        disabled={fullbodyLoading || !fullbodyFrontUrl}
                        onClick={() => void confirmFullbodyFront()}
                        type="button"
                      >
                        确认形象
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {fullbodyZoomUrl && (
                <PortraitLightbox
                  name={answers.name?.trim() || '伙伴'}
                  onClose={() => setFullbodyZoomUrl(null)}
                  url={fullbodyZoomUrl}
                />
              )}
            </div>
          )}

          {phase === 'voice' && voiceStage === 'catalog' && voice && (
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

          {phase === 'voice' && voiceStage === 'catalog' && !voice && (
            <div className="mt-1">
              <p className="text-[13px] text-body">当前系统语言没有可用音色，请先配置支持该语言的 TTS 供应商。</p>
              <button
                className="mt-3 text-xs text-body transition hover:text-strong"
                onClick={() => setVoiceStage('describe')}
                type="button"
              >
                上一步
              </button>
            </div>
          )}

          {phase === 'finishing' && <p className="py-6 text-center text-sm text-strong">正在记住您…</p>}

          {phase === 'greeting' && (
            <div className="mt-4">
              <p className="text-center text-sm text-strong">
                您好，我是{answers.name?.trim() || '您的伙伴'}。很高兴见到您！
              </p>
              {hint && <p className="mt-1 text-center text-[10px] text-muted">{hint}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

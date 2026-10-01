import { playPrepared } from '@/modules/speech'
import { log } from '@/shared/lib/log'

// 引导问题的预渲染题面语音，是引导中唯一的预制语音（DESIGN「引导与后台准备」）。Tag 列表对应 scripts/onboarding-audio/manifest.json，每个 tag 在 $SPIRITAGENT_HOME/audio/onboarding/zh/ 下对应唯一 mp3。
export type OnboardingAudioTag = `onboarding.q${number}`

export async function playOnboardingAudio(tag: OnboardingAudioTag): Promise<boolean> {
  return await playPrepared(
    () => window.spiritagent.media.onboardingAudio.read(tag),
    // 预渲染片段是 onboarding 语音的唯一真相来源——绝不能悄悄回退到运行时 TTS；缺失文件要大声暴露，便于 dev/QA 当场抓到损坏的安装包载荷。
    error => log.error('onboarding-audio', 'missing pre-rendered clip', tag, error)
  )
}

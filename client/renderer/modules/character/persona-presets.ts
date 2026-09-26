export const RELATIONSHIP_PRESETS = ['爱人', '灵魂伴侣', '赛博管家', '知己好友', '宠物', '伙伴'] as const
export const PERSONALITY_PRESETS = [
  '温柔体贴',
  '活泼好动',
  '阳光开朗',
  '优雅知性',
  '冷静理性',
  '毒舌傲娇',
  '腹黑呆萌',
  '高冷清冷'
] as const
export const SPECIES_PRESETS = ['人类', '猫', '狗', '鸟', '鱼', '龙'] as const
export const CHARACTER_GENDER_PRESETS = ['女', '男', '其他', '不指定'] as const
export const SPEAKING_STYLE_PRESETS = ['温柔亲切', '俏皮带点小傲娇', '沉稳简洁', '轻快活泼', '专业干练'] as const
export const VOICE_PRESETS = [
  '甜美女声',
  '温柔女声',
  '活泼少女',
  '清冷御姐',
  '沉稳男声',
  '磁性男声',
  '活力男声',
  '少年音'
] as const
export const USER_GENDER_PRESETS = ['女', '男', '其他', '不愿说'] as const

export type RelationshipPreset = (typeof RELATIONSHIP_PRESETS)[number]
export type PersonalityPreset = (typeof PERSONALITY_PRESETS)[number]
export type SpeakingStylePreset = (typeof SPEAKING_STYLE_PRESETS)[number]

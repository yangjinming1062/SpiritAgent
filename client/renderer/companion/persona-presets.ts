export const RELATIONSHIP_PRESETS = ['亲密的爱人', '灵魂伴侣', '赛博管家', '知己好友', '宠物', '伙伴'] as const
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
export const SPECIES_PRESETS = ['人类', '灵兽', '精灵', '机甲', '幻形'] as const
export const CHARACTER_GENDER_PRESETS = ['女', '男', '其他', '不指定'] as const
export const APPEARANCE_PRESETS = ['优雅古典', '现代利落', '萌系可爱', '冷酷暗黑'] as const
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
export const USER_AGE_BUCKET_PRESETS = ['18 以下', '18-25', '26-35', '36-50', '50+'] as const
export const USER_GENDER_PRESETS = ['女', '男', '其他', '不愿说'] as const

export type RelationshipPreset = (typeof RELATIONSHIP_PRESETS)[number]
export type PersonalityPreset = (typeof PERSONALITY_PRESETS)[number]
export type SpeakingStylePreset = (typeof SPEAKING_STYLE_PRESETS)[number]

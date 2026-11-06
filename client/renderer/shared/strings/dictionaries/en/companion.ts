import type { Dictionary } from '../zh'
export const companion: Pick<Dictionary, 'companion' | 'presets'> = {
  companion: {
    menu: {
      quietOff: (minutes: number) => `End quiet (${minutes} min left)`,
      quietOn: (minutes: number) => `Quiet for ${minutes} min`,
      resetPosition: 'Reset position',
      hide: 'Hide companion',
      activate: 'Activate / sign in',
      openMainWindow: 'Open main window'
    },
    egg: {
      preparing: 'Loading your companion’s look…',
      failed: 'Could not load this look',
      unavailable: 'No visual is available yet',
      retry: 'Retry',
      openVisuals: 'View visuals',
      ariaLabel: 'Companion egg'
    }
  },
  presets: {
    names: {
      companion: 'Companion',
      copywriter: 'Copywriter',
      language_teacher: 'Language teacher',
      general: 'General assistant'
    },
    descriptions: {
      copywriter:
        'Drafts, polishes and organizes copy, emails and meeting notes, keeping the original meaning and fitting the audience and occasion.',
      language_teacher:
        'Explains, corrects and practices at your level and toward your goals, with faithful, natural translations.',
      general:
        'A general conversation without a role: keeps tool use, memory and environment context, and drops the specific persona and professional guidance.'
    }
  }
}

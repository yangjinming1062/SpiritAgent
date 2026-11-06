import type { Dictionary } from '../zh'
export const creation: Pick<Dictionary, 'creation' | 'selfSource' | 'generationActions' | 'studio'> = {
  creation: {
    working: 'Creating',
    review: 'Created, awaiting confirmation',
    success: 'In use',
    paused: 'Creation paused',
    cancelled: 'Creation cancelled',
    unknown: 'Verifying the creation result',
    slow: 'This is taking longer. We are still waiting for the result.',
    preserveCurrent: 'Your current content remains available until you confirm the replacement.',
    canLeave: 'You can leave this page and return later to check progress.',
    inputLocked: 'These inputs cannot be changed during creation. You can still read them.',
    retryLoad: 'Reload preview',
    previewFailed: 'The preview could not load. Your result is still saved.',
    imageLoadFailed: 'Image failed to load',
    checkResult: 'Check existing result',
    finishWaiting: 'I have checked — finish waiting',
    inspectionHint:
      'After checking the latest content, you can close this waiting reminder. This does not cancel accepted work; a delayed task or result may still appear.'
  },
  selfSource: {
    guidanceAction: 'Need to create an image? Get the prompt and references',
    open: 'Use your own image',
    openTitle: 'Use your own image',
    hint: 'Upload a finished image directly, or expand the prompt and references to create it elsewhere. Check the identity, visual style and framing before adopting.',
    referenceTitle: 'Reference images (provide with the prompt)',
    referenceHint:
      'Hand the reference image(s) and prompt to your image tool; use thumbnail actions to copy or save. Click to enlarge.',
    referenceMissing:
      'No reference image is available yet. Complete your companion appearance, then return here to continue.',
    referenceZoom: 'Reference preview',
    refs: {
      avatarSeed: 'Avatar',
      fullbodySeed: 'Full body'
    },
    promptLoading: 'Preparing the prompt…',
    promptFailed: 'Failed to fetch the prompt. Please retry.',
    retryPrompt: 'Refetch prompt',
    copyPrompt: 'Copy prompt',
    copied: 'Copied',
    copyRefImage: 'Copy image',
    copiedRefImage: 'Image copied',
    saveRefImage: 'Save image',
    saveRefImageFailed: 'Failed to save. Please retry.',
    copyRefImageFailed: 'Failed to copy. Please retry.',
    pickTitle: 'Pick the finished image',
    pickImage: 'Pick image',
    replaceImage: 'Replace',
    adopt: 'Use this image',
    adopting: 'Saving…',
    adoptFailed: 'Failed to save. Please retry.',
    useAi: 'Use AI generation'
  },
  generationActions: {
    aiTitle: 'Adjust image',
    edit: 'Refine',
    regenerate: 'Regenerate',
    generate: 'Generate',
    editHint: 'Changes only what you describe; everything else stays',
    editRequiresFeedback: 'Describe the changes first to refine',
    regenerateHint: 'Redraws the whole image from the references and your brief'
  },
  studio: {
    intents: {
      photo: 'A photo',
      photoHint: 'Capture a single moment',
      video: 'A video',
      videoHint: 'Let the scene move',
      motion: 'Make it move',
      motionHint: 'Animate the current outfit'
    },
    descriptionLabel: 'Describe the scene',
    descriptionPlaceholder: 'For example: reading quietly by the window in comfortable clothes',
    describe: 'Interpret',
    describing: 'Interpreting…',
    interpretFailed: "That description couldn't be interpreted. Try again.",
    previewTitle: 'Does this look right',
    confirm: 'Confirm creation',
    uploadConfirm: 'Add to library',
    editDescription: 'Edit description',
    refineLabel: 'Anything to add',
    refinePlaceholder: 'For example: softer light',
    refine: 'Reinterpret',
    infeasible: 'Not possible yet',
    stepAction: 'Create an action first',
    stepOutfit: 'Prepare an outfit first',
    stepScene: 'Create an environment first',
    invalidated: 'Materials changed. Interpret the description again before confirming.',
    activityTitle: 'In progress',
    attentionTitle: 'Needs your attention',
    actionCreate: 'When does it happen, and how does it move',
    actionEditTitle: 'What should change',
    actionSave: 'Save and review',
    createFailed: 'The request was not accepted. Try again.'
  }
}

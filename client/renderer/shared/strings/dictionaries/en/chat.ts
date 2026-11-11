import type { Dictionary } from '../zh'
export const chat: Pick<Dictionary, 'chat' | 'conversation'> = {
  chat: {
    nav: {
      showConversations: 'Show conversations',
      hideConversations: 'Hide conversations',
      companion: 'Companion chat',
      assistant: 'Assistant chat',
      readOnly: 'Task conversation · read-only',
      execution: 'Execution details',
      closeExecution: 'Close execution details',
      pinned: 'Pinned',
      recent: 'Recent conversations',
      running: 'Running',
      loadFailed: 'Conversation could not load. Try again. Your messages and draft have been retained.'
    },
    replaceReply: 'Try another answer',
    replacingReply: 'Preparing another answer…',
    topic: {
      open: 'View original',
      unavailable: 'Original link unavailable',
      like: 'More like this',
      less: 'Less like this',
      undo: 'Click again to undo feedback',
      failed: 'Feedback unavailable. Click to retry.'
    },
    defaultSessionTitle: 'Daily chat',
    inputPlaceholder: 'Say something, or drag a file over',
    typing: 'Typing…',
    openMainSessionFailed: 'Could not open daily chat',

    filesReceived: (count: number) => `${count} file${count === 1 ? '' : 's'} received`,
    attachmentsAdded: (count: number) => `${count} attachment${count === 1 ? '' : 's'} added`,
    filesHandoffFailed: "Couldn't hand over the files. Drop them again.",
    connectionInterrupted: 'Connection interrupted. Replies will sync after reconnecting.',
    sendFailed: 'Send failed',

    media: {
      generating: 'Creating media…',
      generationFailed: 'Generation failed',
      resultUnknown: 'Generation result is not yet confirmed',
      reviewPending: 'Choose a candidate',
      imageLoading: 'Loading image…',
      loadFailed: "Couldn't load media"
    },

    copy: {
      label: 'Copy message',
      failed: 'Failed to copy message'
    },

    contextMenu: {
      label: 'Message actions',
      copySelection: 'Copy selected text'
    },

    fork: {
      label: 'Fork new conversation from this message',
      inFlight: 'Forking new conversation…',
      otherBusy: 'Another fork is in progress',
      failed: 'Fork failed',
      internalError: 'Fork failed: server rejected the request or the network is down'
    },

    undo: {
      label: 'Undo this message',
      inFlight: 'Undoing…',
      otherBusy: 'Another undo is in progress',
      failed: 'Undo failed',
      internalError: 'Undo failed: server rejected the request or the network is down',
      confirmTitle: 'Undo this message?',
      confirmDescription: 'This will also delete every message after it.',
      confirmLabel: 'Undo',
      extraImagesNotRestored: (count: number) =>
        `Only one image can be attached at a time; ${count} other image${count === 1 ? ' was' : 's were'} not restored`
    },

    edit: {
      label: 'Edit message',
      cancel: 'Cancel editing (Esc)',
      send: 'Send changes and regenerate reply',
      hint: 'Regenerate the reply and keep the original attachments',
      retainedImage: 'Original image, kept after sending',
      retainedVideo: 'Original video, kept after sending',
      confirmTitle: 'Continue and send the changes?',
      confirmDescription: 'This is not the last message. Sending the changes will delete every message after it.',
      confirmLabel: 'Send changes',
      stale: 'The conversation has changed. Wait for the reply or select this message again.',
      failed: 'Could not edit the message. Your changes have been kept.'
    },

    attachment: {
      pendingImageAlt: 'Image pending to send',
      loading: 'Loading…',
      sendingImage: 'Sending image…',
      addedImage: 'Image attached',
      removeImage: 'Remove attached image',
      uploading: 'Uploading…',
      uploadInterrupted: 'Upload interrupted. Please retry.',
      ready: 'Ready',
      retry: 'Retry',
      removeVideo: 'Remove attached video',
      fileBadge: 'File',
      removeFile: 'Remove attached file',
      folderBadge: 'Folder',
      removeFolder: 'Remove attached folder'
    },

    slash: {
      confirmRequired: 'This command needs a second confirmation',
      busyStop: 'Please stop the current generation first',
      genericFailed: 'Command failed',
      unknownWithSuggestions: (commands: readonly string[]) =>
        `Unknown command. Try: ${commands.map(s => `/${s}`).join(', ')}`,
      unknown: 'Unknown command',
      inFlight: 'The previous command is still running',
      pendingAttachment: 'Please send or cancel the attachment before running the command',
      confirmTitle: (name: string) => `Run /${name}?`,
      confirmDescription: 'This will affect the message history.',
      confirmLabel: 'Run',
      popoverTitle: 'Commands',
      popoverConfirm: 'Needs confirmation'
    },

    picker: {
      videoFilterName: 'Video files',
      selectVideo: 'Select video',
      imageFilterName: 'Image files',
      selectImage: 'Select image',
      selectFile: 'Select file',
      selectFolder: 'Select folder',
      openFailed: "Couldn't open the file picker"
    },

    voice: {
      retry: 'Voice unavailable',
      retryShort: 'Retry',
      play: 'Play voice',
      pause: 'Pause playback',
      resume: 'Resume playback',
      unheard: 'Unheard',
      listened: 'Listened',
      paused: 'Paused',
      playing: 'Playing',
      loading: 'Loading',
      failed: 'Playback failed',
      progress: 'Playback progress',
      saveFailed: 'Could not save voice playback progress',
      controls: 'Voice controls',
      autoplay: 'Automatically play voice messages',
      restart: 'Play from beginning',
      retryPlayback: 'Retry playback',
      hideTranscript: 'Hide transcript',
      showTranscript: 'Show transcript'
    },

    voiceInput: {
      busy: 'The voice service is busy. Try again.',
      micUnavailable: "Couldn't record from the microphone"
    },

    input: {
      assistantPlaceholder: 'Type a command, ask a question, or drag a file in…',
      readOnlyHint: 'Task conversation · read-only',
      addAttachment: 'Add attachment',
      addFile: 'Add file',
      addFolder: 'Add folder',
      addImage: 'Add image',
      addVideo: 'Add video',
      slashShortcut: 'Slash shortcut',
      slashShortcutHint: 'Slash shortcut (typing / works too)',
      releaseToSendVoice: 'Release to send voice',
      pressToRecordVoice: 'Press and hold to record a voice message',
      startRealtimeCall: 'Start live call',
      stopRealtimeCall: 'End live call',
      muteRealtimeCall: 'Mute call',
      unmuteRealtimeCall: 'Unmute call',
      realtimeCallConnecting: 'Connecting call…',
      realtimeCallListening: 'Live call · Listening',
      realtimeCallThinking: 'Live call · Thinking',
      realtimeCallSpeaking: 'Live call · Speaking',
      realtimeCallMuted: 'Live call · Microphone muted',
      realtimeCallError: 'Live call failed',
      stopGenerating: 'Stop generating',
      stopPending: 'Stopping, awaiting confirmation',
      sendMessage: 'Send message',
      sendMessageShortcut: 'Send message (Enter)'
    },

    emptyHint: 'Say something, or send a file, image or video',

    time: {
      weekdays: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as readonly string[],
      yesterdayAt: (time: string) => `Yesterday ${time}`,
      weekdayAt: (weekday: string, time: string) => `${weekday} ${time}`,
      monthDayAt: (month: number, day: number, time: string) => `${month}/${day} ${time}`,
      fullDateAt: (year: number, month: number, day: number, time: string) => `${year}/${month}/${day} ${time}`
    },

    reasoning: {
      thinkingStreaming: 'Thinking…',
      reasoningStreaming: 'Reasoning…',
      done: 'Thinking complete',
      collapse: 'Collapse',
      expand: 'Show details'
    },

    summary: {
      emptyBody: '(no summary content)',
      cancelled: 'Stopped'
    },

    tools: {
      busy: (name: string) => `Working… (${name})`,
      completed: (count: number) => `Completed ${count} step${count === 1 ? '' : 's'}`,
      genericName: 'Tool'
    },

    submit: {
      videoUploadFailed: 'Video upload failed, please pick another',
      videoUploading: 'Video is uploading, please wait…',
      unknownCommand: (name: string) => `Unknown command: /${name}. Try /help to see available commands.`,
      displayVideo: '(video)',
      promptVideo: 'Please look at this video',
      promptImage: 'Please look at this image',
      fileInlinePrefix: (name: string, path: string) => `[file: ${name}] ${path}`,
      folderInlinePrefix: (name: string, path: string) => `[folder: ${name}] ${path}`,
      attachmentsHeading: (names: string) => `Attachments: ${names}`,
      attachmentsJoiner: ', '
    },

    params: {
      openPanel: 'Advanced conversation settings',
      tabs: {
        context: 'Context',
        temperature: 'Temperature',
        reasoning: 'Reasoning'
      },
      temperaturePresets: {
        precise: 'Precise',
        balanced: 'Balanced',
        divergent: 'Creative'
      },
      temperatureStyles: {
        precise: 'Precise',
        balanced: 'Balanced',
        divergent: 'Creative'
      },
      reasoningOptions: {
        none: 'Off',
        minimal: 'Minimal',
        low: 'Low',
        medium: 'Medium',
        high: 'High',
        xhigh: 'X-High',
        max: 'Max',
        ultra: 'Ultra'
      },
      reasoningHints: {
        high: 'Deep reasoning — suited for complex plans and proofs.',
        low: 'Quick thinking — best for simple Q&A.',
        medium: 'Standard reasoning — good for writing and language learning.',
        none: 'Direct answers — fastest response.',
        minimal: 'Barely-there thinking — just above off, for the simplest prompts.',
        xhigh: 'Extra-deep reasoning — for research-grade problems and hard proofs.',
        max: 'Near-ceiling reasoning — think as hard as the provider allows.',
        ultra: 'Highest product level; falls back to the provider’s top supported level automatically.'
      },

      thresholdMarker: (pct: number) => `Auto-compress threshold (${pct}%)`,

      manualCompressFailed: 'Failed to manually compress context',

      thresholdSliderHeading: 'Context window load',
      thresholdSliderSubheading: '/ auto-compress line',
      thresholdSliderTriggerLabel: 'Trigger point:',
      thresholdSliderAria: 'Auto-compress threshold (drag left/right to adjust)',
      thresholdSliderMin: '30% tighter compression',
      thresholdSliderHint: 'Drag the handle left/right to adjust the threshold',
      thresholdSliderMax: '100% full capacity',
      thresholdSliderDescription:
        'When context reaches the chosen ratio, older history is automatically summarised in the background to free up space and keep the conversation coherent.',

      saveFailed: 'Could not save session parameters. Try again.',
      resetConfirm: 'This conversation now follows global settings',
      compressSuccessMore: (count: number) => `Successfully tidied and compressed ${count} earlier messages`,
      compressNotNeeded: 'Not much history to compress yet',

      sessionScopeBadge: 'Current session',
      sessionScopeBadgeTitle: 'Overrides apply to this conversation. Reset to defaults to follow global settings.',
      closePanel: 'Close panel',

      statsUsed: 'Used',
      statsMax: 'Max capacity',
      statsPercent: 'Usage',
      statsTokens: 'Tokens',
      statsNodeAt: (pct: number) => `Threshold ${pct}%`,

      cacheUsageTitle: 'Last completed turn · cache hit rate',
      cacheUsagePending: 'No turn statistics in this run yet',
      cacheUsageUnavailable: 'Not provided by the provider',
      cacheUsagePartial: 'Partial data',
      cacheUsageTokens: (cached: string, input: string) => `Cached / input: ${cached} / ${input} tokens`,
      cacheUsageRequests: (reported: number, total: number) => `Reported requests: ${reported} / ${total}`,

      statusHealthy: 'Plenty of context headroom for smooth conversation.',
      statusWarning: 'Context is filling up — nearing the auto-compress threshold; tidy anytime.',
      statusCritical: 'Context is heavy — past the auto-compress line; compress now.',

      compressing: 'Compressing earlier messages…',
      compressAction: 'Compress context now',

      temperatureLabel: 'Sampling temperature',
      temperatureSliderAria: 'Sampling temperature',
      temperatureScaleMin: '0.00 precise',
      temperatureScaleMid: '0.70 balanced',
      temperatureScaleMax: '1.00 creative',
      temperatureHint: 'Lower is more deterministic, higher is more creative. Applies to this session only.',

      reasoningLabel: 'Reasoning depth',
      reasoningFallbackLabel: 'Off',

      scopeNote: 'Overrides this conversation. Reset to follow global settings.',
      scopeNoteTitle: 'Overrides apply to this conversation. Reset to defaults to follow global settings.',
      resetButtonTitle: 'Clear this conversation’s overrides and follow global settings',
      resetButton: 'Reset to defaults'
    },

    presetPicker: {
      title: 'Pick a preset for the new conversation',
      intro:
        'The system prompt for this conversation is fixed from here; you can still rename, archive, or delete it later.',
      confirm: 'Create',
      cancel: 'Cancel',
      fetchFailed: 'Failed to load presets — try again.',
      pickOne: 'Please pick a preset first'
    },

    sessionRename: {
      action: 'Rename',
      inputLabel: 'Conversation name',
      placeholder: 'Enter a name',
      hint: 'Enter to save · Esc to cancel',
      forbidden: 'System conversations cannot be renamed',
      failed: 'Rename failed, restored previous name'
    }
  },
  conversation: {
    sessionSidebar: {
      title: 'Sessions',
      new: 'New conversation',
      searchAria: 'Search conversations',
      searchPlaceholder: 'Search conversations…',
      searchResultsHeading: 'Search results',
      searching: 'Searching…',
      noMatch: 'No matching conversations',
      badgeArchived: 'Archived',
      systemHeading: 'System conversations',
      loadingSystem: 'Loading system conversations…',
      noSystem: 'No system conversations',
      ordinaryHeading: 'Ordinary conversations',
      loadingSessions: 'Loading conversations…',
      noOrdinary: 'No ordinary conversations yet — tap above to create one',
      systemSessionFallback: 'My companion',
      newSessionFallback: 'New conversation',
      messageCount: (n: number) => `${n} messages`,
      sortOptions: {
        recent: 'Sort by recent activity',
        created: 'Sort by creation time',
        messages: 'Sort by message count'
      },
      actions: {
        pin: 'Pin',
        unpin: 'Unpin',
        archive: 'Archive',
        restore: 'Restore',
        delete: 'Delete'
      },
      time: {
        now: 'Just now',
        todayPrefix: (time: string) => `Today ${time}`,
        yesterdayPrefix: (time: string) => `Yesterday ${time}`,
        dateFormat: (month: number, day: number, time: string) => `${month}/${day} ${time}`
      },
      archive: {
        loading: 'Loading…',
        empty: 'No archived conversations',
        collapse: 'Hide archived conversations',
        expand: (count: number) => `Archived conversations${count > 0 ? ` (${count})` : ''}`
      }
    },
    runRail: {
      label: 'Run trace',
      subtitle: (steps: number) => `${steps} steps · this round`,
      idleSubtitle: 'Idle',
      toolsTitle: 'Tools this round',
      toolsSubtitleIdle: 'Not yet started',
      toolsSubtitleSteps: (steps: number) => `${steps} steps`,
      preparing: 'Preparing…',
      toolsEmpty: "This round hasn't started yet",
      artifactsTitle: 'Session artifacts',
      artifactsSubtitleEmpty: 'None yet',
      artifactsSubtitleCount: (count: number) => `${count} items`,
      artifactsEmpty: 'No images or videos generated this round'
    }
  }
}

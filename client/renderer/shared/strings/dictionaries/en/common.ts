import type { Dictionary } from '../zh'
export const common: Pick<Dictionary, 'brand' | 'common' | 'notifications' | 'errors' | 'ui'> = {
  brand: {
    name: 'SpiritAgent',
    fullName: 'SpiritAgent Desktop'
  },
  common: {
    actionVariants: {
      title: 'Creation progress by view',
      readyWithStatus: (status: string) => `Playable · ${status}`,
      remakeFailed: 'Remake failed',
      statuses: {
        not_generated: 'Not created',
        queued: 'Waiting to start',
        processing: 'Creating',
        ready: 'Playable',
        failed: 'Creation failed',
        blocked: 'Creation unavailable',
        disabled: 'Disabled',
        excluded: 'Removed'
      }
    },
    videoQueue: {
      waiting: 'Video queued',
      rate_limited: 'Service busy, waiting to retry',
      blocked: 'Waiting for generation results to be verified'
    },
    done: 'Done',
    moreActions: 'More actions',
    creationDetails: 'Creation details',
    companionControl: {
      title: 'Life area',
      show: 'Show life area',
      left: 'Left side',
      right: 'Right side',
      saveFailed: 'Could not save life area display settings. Try again.'
    },
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    clear: 'Clear',
    close: 'Close',
    copied: 'Copied',
    copyFailed: 'Copy failed. Try again.',
    loading: 'Loading…',
    maximize: 'Maximize',
    minimize: 'Minimize',
    remove: 'Remove',
    restore: 'Restore',
    retry: 'Retry',
    pageRestoreFailed: 'Could not restore the page',
    pickDate: 'Pick a date',
    datePicker: { year: 'Year', month: 'Month', day: 'Day' },
    imagePick: {
      tooLarge: 'This image is too large. Choose a smaller one.',
      readFailed: "Couldn't read the selected image. Try again."
    },
    processing: 'Processing…',
    operationFailed: 'The operation could not finish. Try again.',
    saveChanges: 'Save changes',
    unsavedChanges: 'Unsaved changes',
    keepMyEdits: 'Reviewed, keep my edits',
    forget: 'Forget',
    changedElsewhere: 'These details changed on another device. Review the latest values before saving.',
    view: 'View',
    back: 'Back',
    search: 'Search',
    previous: 'Previous',
    next: 'Next',
    loadMore: 'Load more',
    totalItems: (count: number) => `${count} item${count === 1 ? '' : 's'}`,
    pageOf: (page: number, total: number) => `Page ${page} of ${total}`,
    refresh: 'Refresh'
  },
  notifications: {
    region: 'Notifications',
    hide: 'Hide',
    show: 'Show',
    more: (count: number) => `${count} more notifications`,
    clearAll: 'Clear all',
    dismiss: 'Dismiss notification',
    details: 'Details',
    copyDetail: 'Copy details',
    errors: {
      methodNotAllowed: (brandFullName: string) =>
        `The server rejected this request (405 Method Not Allowed). Check whether ${brandFullName} needs an update.`,
      microphonePermission: 'Microphone permission was denied.'
    },
    voice: {
      invalidTitle: 'Voice no longer available',
      invalidMessage: (name: string) =>
        `The voice "${name}" you previously selected is unavailable, so the default voice is being used for now. Choose another in Companion › Voice.`,
      invalidAction: 'Choose a voice'
    },
    system: {
      view: 'View',
      scheduledTask: 'Scheduled task',
      imageReady: 'Image ready — click to view',
      videoReady: 'Video ready — click to view'
    }
  },
  errors: {
    boundaryTitle: 'Something went wrong',
    boundaryDesc: 'This view hit an unexpected error. Your conversations and settings are safe.',
    reloadWindow: 'Reload window'
  },
  ui: {
    lightbox: {
      backdropAria: 'Click backdrop to close',
      closePreview: 'Close preview',
      zoomIn: 'Zoom in',
      zoomOut: 'Zoom out',
      zoomReset: 'Reset',
      zoomPercent: (n: number) => `${n}%`,
      zoomHint: 'Scroll to zoom · drag to pan · double-click to zoom in/out'
    },
    player: {
      fullscreen: 'Fullscreen',
      mute: 'Mute',
      pause: 'Pause',
      play: 'Play',
      progress: 'Playback progress',
      unmute: 'Unmute'
    },
    search: {
      clear: 'Clear search'
    }
  }
}

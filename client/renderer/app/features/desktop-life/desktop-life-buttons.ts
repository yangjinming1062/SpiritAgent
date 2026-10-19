import {
  BTN_GHOST as PANEL_BTN_GHOST,
  BTN_PRIMARY as PANEL_BTN_PRIMARY,
  BTN_SUBTLE as PANEL_BTN_SUBTLE
} from '@/shared/panel'

import styles from './desktop-life.module.css'

export const BTN_PRIMARY = `${PANEL_BTN_PRIMARY} ${styles.primaryButton}`
export const BTN_SUBTLE = `${PANEL_BTN_SUBTLE} ${styles.secondaryButton}`
export const BTN_GHOST = `${PANEL_BTN_GHOST} ${styles.ghostButton}`

import { useStore } from '@nanostores/react'
import type { ComponentProps } from 'react'

import { $locale } from '@/shared/store/locale'

import { ArchiveSearchFeedback as Feedback, ArchiveSearchForm as Form } from '../../../shared/archive-search-form'

import styles from './archive-search.module.css'
import { BTN_SUBTLE, INPUT_CLASS } from './palette'

export function ArchiveSearchForm(props: ComponentProps<typeof Form>) {
  return <Form {...props} classes={{ ...styles, input: INPUT_CLASS, button: BTN_SUBTLE }} locale={useStore($locale)} />
}

export function ArchiveSearchFeedback(props: ComponentProps<typeof Feedback>) {
  return <Feedback {...props} className={styles.feedback} locale={useStore($locale)} />
}

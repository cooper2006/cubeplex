'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, RotateCw } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface WeComBindingData {
  code: string
  instruction: string
  expires_in: number
}

interface Props extends WizardStepProps {
  wsId?: string
}

/** Fallback only — the backend returns the real TTL in `expires_in`. */
const DEFAULT_TTL_SECONDS = 300

let _bindingRefreshKey = 0

/**
 * Step that shows the WeCom binding code and instructions.
 * User copies the code and sends /connect <code> to the bot in WeCom mobile.
 */
export function StepWeComBinding({ descriptor, form, wsId }: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const client = useMemo(() => createApiClient(''), [])
  const [bindingData, setBindingData] = useState<WeComBindingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [remaining, setRemaining] = useState<number>(DEFAULT_TTL_SECONDS)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function fetchBinding() {
    try {
      setLoading(true)
      setError(null)
      const res = await client.postRaw(
        `/api/v1/ws/${wsId}/im/wecom/connect`,
        {
          platform: 'wecom',
          bot_id: form.bot_id || '',
          bot_name: form.bot_name?.trim() || '',
          secret: form.secret || '',
          acting_user_id: 'self',
        }
      )
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error((body as { detail?: string } | null)?.detail || `HTTP ${res.status}`)
      }
      const data = (await res.json()) as WeComBindingData
      setBindingData(data)
      startCountdown(data.expires_in)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to get binding code'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  function startCountdown(expiresIn?: number) {
    if (intervalRef.current) clearInterval(intervalRef.current)
    const ttl = expiresIn && expiresIn > 0 ? expiresIn : DEFAULT_TTL_SECONDS
    const now = Date.now() / 1000
    const secsLeft = Math.max(0, ttl - (Date.now() / 1000 - now))
    setRemaining(Math.ceil(secsLeft))
    if (secsLeft <= 0) {
      void fetchBinding()
      return
    }
    intervalRef.current = setInterval(() => {
      const left = Math.ceil(ttl - (Date.now() / 1000 - now))
      setRemaining(left)
      if (left <= 0) {
        if (intervalRef.current) clearInterval(intervalRef.current)
        void fetchBinding()
      }
    }, 1000)
  }

  useEffect(() => {
    void fetchBinding()
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [wsId])

  if (loading && !bindingData) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <Loader2 className="size-4 animate-spin" />
        <p>{t('im.wizard.wecom.binding.loading')}</p>
      </div>
    )
  }

  if (error || !bindingData?.code) {
    return (
      <div className="space-y-3">
        <Alert variant="destructive">
          <AlertDescription>{error || t('im.wizard.wecom.binding.noCode')}</AlertDescription>
        </Alert>
        <Button onClick={fetchBinding} variant="outline" size="sm">
          <RotateCw className="size-3 mr-1" />
          {t('im.wizard.wecom.binding.refresh')}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-muted/30 p-4 text-center">
        <p className="text-sm text-muted-foreground mb-2">{t('im.wizard.wecom.binding.title')}</p>
        <div className="font-mono text-2xl font-bold tracking-wider">{bindingData.code}</div>
        <p className="text-xs text-muted-foreground mt-2">{bindingData.instruction}</p>
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{t('im.wizard.wecom.binding.expiresIn', { seconds: remaining })}</span>
        <Button
          onClick={fetchBinding}
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
        >
          <RotateCw className="size-3 mr-1" />
          {t('im.wizard.wecom.binding.refresh')}
        </Button>
      </div>
    </div>
  )
}

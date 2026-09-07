'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, RotateCw } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { QRCodeSVG } from 'qrcode.react'

import { createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface WeChatQRData {
  code: string
  qrcode_url: string | null
  instruction: string
  expires_in: number
  qr_generated_at: number | null
}

interface Props extends WizardStepProps {
  wsId: string
}

const QR_TTL_SECONDS = 20

let _qrRefreshKey = 0

/**
 * Step that shows the WeChat QR code and binding instructions.
 * User scans QR, sends /connect <code> to bot, then clicks Done.
 */
export function StepWechatQR({
  descriptor,
  form,
  wsId,
  onNext,
}: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const client = useMemo(() => createApiClient(''), [])
  const [qrData, setQrData] = useState<WeChatQRData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [remaining, setRemaining] = useState<number>(QR_TTL_SECONDS)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function fetchQR() {
    try {
      setLoading(true)
      setError(null)
      const res = await client.postRaw(`/api/v1/ws/${wsId}/im/wechat/connect`, {})
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error((body as { detail?: string } | null)?.detail || `HTTP ${res.status}`)
      }
      const data = (await res.json()) as WeChatQRData
      setQrData(data)
      startCountdown(data.qr_generated_at)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to get QR code'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  function startCountdown(generatedAt: number | null) {
    if (intervalRef.current) clearInterval(intervalRef.current)
    const now = generatedAt ? generatedAt : Date.now() / 1000
    const secsLeft = Math.max(0, QR_TTL_SECONDS - (Date.now() / 1000 - now))
    setRemaining(Math.ceil(secsLeft))
    if (secsLeft <= 0) {
      void fetchQR()
      return
    }
    intervalRef.current = setInterval(() => {
      const left = Math.ceil(QR_TTL_SECONDS - (Date.now() / 1000 - now))
      setRemaining(left)
      if (left <= 0) {
        if (intervalRef.current) clearInterval(intervalRef.current)
        void fetchQR()
      }
    }, 1000)
  }

  useEffect(() => {
    void fetchQR()
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [wsId])

  if (loading && !qrData) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <Loader2 className="size-4 animate-spin" />
        <p>{t('im.wizard.wechat.qr.loading')}</p>
      </div>
    )
  }

  if (error || !qrData?.qrcode_url) {
    return (
      <div className="space-y-3">
        <Alert variant="destructive">
          <AlertDescription>{error || t('im.wizard.wechat.qr.noQr')}</AlertDescription>
        </Alert>
        <Button onClick={fetchQR} variant="outline" size="sm">
          <RotateCw className="size-3 mr-1" />
          {t('im.wizard.wechat.qr.refresh')}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col items-center gap-3 rounded-lg border bg-muted/30 p-4">
        <div className="flex h-48 w-48 items-center justify-center overflow-hidden rounded-lg bg-background">
          <QRCodeSVG
            key={`${qrData.qrcode_url}-${++_qrRefreshKey}`}
            value={qrData.qrcode_url}
            size={192}
            level="M"
            includeMargin={false}
          />
        </div>
        <div className="text-center text-sm text-muted-foreground">
          <p className="font-medium">{t('im.wizard.wechat.qr.scanTitle')}</p>
          <p>{t('im.wizard.wechat.qr.scanHint')}</p>
        </div>
      </div>

      <div className="rounded-md bg-muted px-3 py-2 text-center">
        <p className="text-xs text-muted-foreground">{t('im.wizard.wechat.qr.sendHint')}</p>
        <p className="font-mono text-sm">{qrData.code ? `/connect ${qrData.code}` : '...'}</p>
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{t('im.wizard.wechat.qr.expiresIn', { seconds: remaining })}</span>
        <Button
          onClick={fetchQR}
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
        >
          <RotateCw className="size-3 mr-1" />
          {t('im.wizard.wechat.qr.refresh')}
        </Button>
      </div>

      <div className="flex justify-end">
        <Button onClick={onNext}>
          {t('im.action.connected')}
        </Button>
      </div>
    </div>
  )
}

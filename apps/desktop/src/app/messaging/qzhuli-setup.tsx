import { useEffect, useState } from 'react'

import { getQzhuliBindStatus, startQzhuliBind, updateMessagingPlatform } from '@/api/messaging'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { Check, QrCode } from '@/lib/icons'
import type { MessagingPlatformInfo, QzhuliBindStartResponse, QzhuliBindStatusResponse } from '@/types/hermes'

// hermes-dev: Qzhuli 扫码绑定面板（插件平台 qzhuli，desktop 设置界面入口）。
// 绑定轮询由 backend 代理（renderer 不直连 Q助理，避免 CORS）；绑定成功后凭据
// 经 updateMessagingPlatform 写入 ~/.hermes/.env。联调环境固定为生产（release）。
const QZHULI_ENVIRONMENT = 'release'

type Phase = 'applying' | 'done' | 'idle' | 'starting' | 'waiting'

async function renderQr(payload: string): Promise<string> {
  // Lazy: the QR encoder is only needed while a binding is on screen.
  const QRCode = await import('qrcode')

  return QRCode.toDataURL(payload, { errorCorrectionLevel: 'M', margin: 1, width: 224 })
}

export interface QzhuliBindSetupProps {
  /** Called after the backend wrote the four QZHULI_* env vars. */
  onApplied?: () => void
  platform: MessagingPlatformInfo
  /** Request-shaped profile scope (undefined → active profile). */
  scopeProfile: string | undefined
}

/**
 * Qzhuli "Quick setup": Hermes mints a bind key, shows its QR payload
 * (`{"type":"imnut_bind","key":…,"id":2}`), polls the Q助理 bind status via
 * the backend until the user scans and confirms, then writes the credentials.
 */
export function QzhuliBindSetup({ onApplied, platform, scopeProfile }: QzhuliBindSetupProps) {
  const { t } = useI18n()
  const q = t.messaging.qzhuliBind
  const [setup, setSetup] = useState<null | QzhuliBindStartResponse>(null)
  const [qrDataUrl, setQrDataUrl] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState('')

  const reset = () => {
    setSetup(null)
    setQrDataUrl('')
    setPhase('idle')
    setError('')
  }

  const start = async () => {
    setPhase('starting')
    setError('')

    try {
      const result = await startQzhuliBind(QZHULI_ENVIRONMENT, scopeProfile)
      const dataUrl = await renderQr(result.qr_payload)
      setSetup(result)
      setQrDataUrl(dataUrl)
      setPhase('waiting')
    } catch (startError) {
      setPhase('idle')
      setError(q.saveFailed(String(startError)))
    }
  }

  // Poll until the Q助理 app confirms the binding. A transient fetch error
  // keeps polling with a visible hint; bound credentials are saved and we stop.
  useEffect(() => {
    if (!setup || phase !== 'waiting') {
      return
    }

    let cancelled = false
    let timer: null | number = null

    const poll = async () => {
      try {
        const status = await getQzhuliBindStatus(setup.bind_key, setup.environment, scopeProfile)

        if (cancelled) {
          return
        }

        if (status.status === 'bound') {
          await saveCredentials(status)

          return
        }

        setError('')
        timer = window.setTimeout(() => void poll(), 2500)
      } catch (pollError) {
        if (cancelled) {
          return
        }

        setError(q.stillWaiting(String(pollError)))
        timer = window.setTimeout(() => void poll(), 2500)
      }
    }

    const saveCredentials = async (status: Extract<QzhuliBindStatusResponse, { status: 'bound' }>) => {
      setPhase('applying')
      setError('')

      try {
        await updateMessagingPlatform(
          'qzhuli',
          {
            // hermes-dev: enabled=true 必须一并提交——否则 platforms.qzhuli.enabled 不写入，
            // gateway 只启动已启用平台，adapter 永远不会连接。
            enabled: true,
            env: {
              QZHULI_ENVIRONMENT,
              QZHULI_SENDER_CID: status.cid,
              QZHULI_CONV_ID: status.conversation_id,
              QZHULI_WS_TOKEN: status.bind_token
            }
          },
          scopeProfile
        )

        if (!cancelled) {
          reset()
          setPhase('done')
          onApplied?.()
        }
      } catch (applyError) {
        if (!cancelled) {
          setPhase('waiting')
          setError(q.saveFailed(String(applyError)))
        }
      }
    }

    timer = window.setTimeout(() => void poll(), 1000)

    return () => {
      cancelled = true

      if (timer !== null) {
        window.clearTimeout(timer)
      }
    }
    // onApplied 是父组件 inline 回调，每次渲染都变；仅需在绑定完成时触发一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, q, scopeProfile, setup])

  return (
    <div className="rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-surface-secondary,transparent) p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[length:var(--conversation-text-font-size)] font-medium">{q.quickSetup}</span>
          </div>
          <p className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
            {q.quickHelp}
          </p>
        </div>
        {phase === 'idle' && (
          <Button onClick={() => void start()} size="sm">
            <QrCode />
            {q.createWithQr}
          </Button>
        )}
        {phase === 'starting' && (
          <Button disabled size="sm">
            {q.starting}
          </Button>
        )}
      </div>

      {platform.configured && phase === 'idle' && (
        <p className="mt-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-muted-foreground">
          {q.replaceWarning}
        </p>
      )}

      {error && <ErrorBanner className="mt-3">{error}</ErrorBanner>}

      {setup && qrDataUrl && (
        <div className="mt-3 grid gap-4 border-t border-(--ui-stroke-secondary) pt-3 lg:grid-cols-[minmax(0,1fr)_240px]">
          <div className="grid content-start gap-3">
            {phase === 'waiting' && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="warn">{q.waiting}</Badge>
                <span className="text-xs text-muted-foreground">{q.scanHint}</span>
              </div>
            )}

            {phase === 'applying' && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="warn">{q.saving}</Badge>
              </div>
            )}
          </div>

          <div className="grid justify-items-center gap-2">
            <img
              alt={q.scanHint}
              className="rounded-lg border border-(--ui-stroke-secondary)"
              height={224}
              src={qrDataUrl}
              width={224}
            />
          </div>
        </div>
      )}

      {phase === 'done' && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-(--ui-stroke-secondary) pt-3">
          <Badge variant="success">
            <Check />
            {q.connected}
          </Badge>
          <span className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-muted-foreground">
            {q.saved}
          </span>
        </div>
      )}
    </div>
  )
}

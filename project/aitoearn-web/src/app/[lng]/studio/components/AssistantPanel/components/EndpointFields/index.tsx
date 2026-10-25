/**
 * EndpointFields：模型端点的三个输入框。
 *
 * 首次进入的引导块和头部设置弹层共用一份字段，避免两处各写一遍再漂移。
 * idPrefix 让两处同时挂载时 id 不冲突，Label htmlFor 才点得动。
 */
'use client'

import type { AgentEndpoint } from '../../utils'
import { useTransClient } from '@/app/i18n/client'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface EndpointFieldsProps {
  idPrefix: string
  value: AgentEndpoint
  onChange: (next: AgentEndpoint) => void
}

export function EndpointFields({ idPrefix, value, onChange }: EndpointFieldsProps) {
  const { t } = useTransClient('studio')

  return (
    <>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${idPrefix}-base-url`} className="text-xs">{t('assistant.endpoint.baseUrl')}</Label>
        <Input
          id={`${idPrefix}-base-url`}
          value={value.baseUrl}
          placeholder="https://api.example.com/v1"
          onChange={event => onChange({ ...value, baseUrl: event.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${idPrefix}-api-key`} className="text-xs">{t('assistant.endpoint.apiKey')}</Label>
        <Input
          id={`${idPrefix}-api-key`}
          type="password"
          value={value.apiKey}
          onChange={event => onChange({ ...value, apiKey: event.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${idPrefix}-model`} className="text-xs">{t('assistant.endpoint.model')}</Label>
        <Input
          id={`${idPrefix}-model`}
          value={value.model}
          onChange={event => onChange({ ...value, model: event.target.value })}
        />
      </div>
    </>
  )
}

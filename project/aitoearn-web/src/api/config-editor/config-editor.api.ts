import type { ConfigEditorConfigDto, ConfigEditorConfigVo } from './config-editor.types'
import { getAccountListApi } from '@/api/accounts/account.api'
import http from '@/utils/request'
import { ConfigEditorServiceTarget } from './config-editor.types'

function getConfigEditorRoute(target: ConfigEditorServiceTarget, path = '') {
  const baseRoute = target === ConfigEditorServiceTarget.Ai ? 'ai/config' : 'config'
  return path ? `${baseRoute}/${path}` : baseRoute
}

export function getConfigEditorConfigApi(target = ConfigEditorServiceTarget.Server, silent = true) {
  return http.get<ConfigEditorConfigVo>(getConfigEditorRoute(target), undefined, silent)
}

export function validateConfigEditorConfigApi(data: ConfigEditorConfigDto, target = ConfigEditorServiceTarget.Server, silent = true) {
  return http.post<void>(getConfigEditorRoute(target, 'validate'), data, silent)
}

export function saveConfigEditorConfigApi(data: ConfigEditorConfigDto, target = ConfigEditorServiceTarget.Server, silent = true) {
  return http.put<void>(getConfigEditorRoute(target), data, silent)
}

export function restartConfigEditorServiceApi(target = ConfigEditorServiceTarget.Server, silent = true) {
  return http.post<void>(getConfigEditorRoute(target, 'restart'), undefined, silent)
}

export async function checkConfigEditorConfigReadyApi(target = ConfigEditorServiceTarget.Server, timeoutMs = 2500) {
  const timeout = new Promise<false>(resolve => setTimeout(() => resolve(false), timeoutMs))

  if (target === ConfigEditorServiceTarget.Server) {
    return Promise.race([
      getAccountListApi(true)
        .then(response => !!response && response.code === 0)
        .catch(() => false),
      timeout,
    ])
  }

  const readiness = getConfigEditorConfigApi(target, true)
    .then(response => !!response && response.code === 0)
    .catch(() => false)

  return Promise.race([readiness, timeout])
}

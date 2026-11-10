/**
 * Cliente HTTP único do app: manda o JWT em toda chamada e avisa quando a
 * sessão acabou (401).
 *
 * Ele não importa o store de login para não criar dependência circular
 * (o store usa este cliente para fazer o login). É o store que se registra
 * aqui, em `configureAuth`, dizendo como pegar o token e o que fazer num 401.
 */

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? ''
export const API_BASE = configuredBase.replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface AuthHooks {
  getToken: () => string | null
  onUnauthorized: () => void
}

let authHooks: AuthHooks = {
  getToken: () => null,
  onUnauthorized: () => {},
}

export function configureAuth(hooks: AuthHooks): void {
  authHooks = hooks
}

interface RequestOptions {
  /** `false` para rotas públicas, como o próprio login. */
  auth?: boolean
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  return (await (await requestResponse(path, init, options)).json()) as T
}

/** Mesmo contrato de autenticação, preservando o corpo para streaming. */
export async function requestResponse(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<Response> {
  const useAuth = options.auth !== false
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')

  const token = useAuth ? authHooks.getToken() : null
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (!response.ok) {
    // 401 numa rota autenticada é token vencido ou inválido: a sessão acaba
    // e o App leva a pessoa de volta ao login.
    if (response.status === 401 && useAuth) authHooks.onUnauthorized()
    throw new ApiError(await errorDetail(response), response.status)
  }

  return response
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    // O FastAPI manda `detail` como texto nos erros nossos e como lista nos
    // erros de validação; a lista não serve para mostrar na tela.
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Mantém a mensagem HTTP quando a resposta não for JSON.
  }
  return `Erro HTTP ${response.status}`
}

/** URL de WebSocket no mesmo host da API (ou do proxy do Vite/nginx). */
export function wsUrl(path: string, params: Record<string, string>): string {
  const search = new URLSearchParams(params).toString()

  if (!API_BASE) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${path}?${search}`
  }

  const url = new URL(API_BASE, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `${url.pathname.replace(/\/$/, '')}${path}`
  url.search = `?${search}`
  return url.toString()
}

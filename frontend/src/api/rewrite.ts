import { ApiError, checkVersion, request, type ClientOptions } from './client'
import { isRewriteResponse, type RewriteRequest, type RewriteResponse } from './contract'

/** POST /rewrite: one flash-lite call that splits a message into question blocks (never answers them). */
export async function postRewrite(opts: ClientOptions, req: RewriteRequest, timeoutMs = 25_000): Promise<RewriteResponse> {
  const body = await request(
    opts,
    '/rewrite',
    { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(req) },
    timeoutMs,
  )
  if (!isRewriteResponse(body)) throw new ApiError('The rewrite does not match the contract', 200, 'invalid_response', body)
  checkVersion(body.contract_version, body)
  return body
}

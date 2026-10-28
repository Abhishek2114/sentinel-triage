# Upstream Rate Limiting Runbook

## Symptoms
- HTTP `429 Too Many Requests` from a third-party or internal API
- `rate limit exceeded` / `quota exhausted` / `throttled` messages
- `Retry-After` headers and growing retry queues
- Failures concentrated on calls to one provider

## Common Causes
1. **Traffic spike** – A batch job or marketing event multiplies calls
2. **Retry storm** – Clients retry immediately without backoff
3. **Quota lowered** – Provider plan or limit changed
4. **Shared key** – Several services drain one API key's quota

## Diagnosis Steps
1. Count 429s per caller and per endpoint
2. Read `Retry-After` and rate-limit headers in responses
3. Check the provider's status page and quota dashboard
4. Look for batch jobs or deploys that started at the same time

## Remediation
- **Immediate**: Pause the batch job; honour Retry-After with exponential backoff and jitter
- **Short-term**: Add a client-side token bucket; cache repeated responses
- **Long-term**: Separate keys per service, request a quota increase, add circuit breakers

# CPU Spike Runbook

## Symptoms
- Response times exceed SLA thresholds (> 500ms p99)
- Worker thread pool exhaustion
- Circuit breakers tripping on downstream services
- CPU utilization above 90% sustained

## Common Causes
1. **Runaway query or computation** – A new code path with O(n²) complexity
2. **Garbage collection storms** – Large heap with insufficient GC tuning
3. **Infinite loop or tight spin** – Bug in retry logic
4. **Traffic spike** – Organic or attack-driven burst

## Diagnosis Steps
1. Check CPU per pod: `kubectl top pods -n production`
2. Look at flame graphs from continuous profiler
3. Check recent deployments: `kubectl rollout history`
4. Review rate limiter metrics

## Remediation
- **Immediate**: Scale horizontally with HPA or add spot instances
- **Short-term**: Identify hot code path and add caching or circuit breaking
- **Long-term**: Fix algorithmic complexity, add load shedding

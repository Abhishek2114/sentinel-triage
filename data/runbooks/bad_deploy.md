# Bad Deploy Runbook

## Symptoms
- New error templates appearing in logs (never-seen-before patterns)
- HTTP 500 rates spiking immediately after deploy
- Panic/crash logs from new code paths
- Version mismatch errors between services
- Health check failures triggering rollbacks

## Common Causes
1. **Untested code path** – Edge case not covered by tests
2. **Config mismatch** – Missing environment variable or wrong value
3. **Dependency incompatibility** – Library version conflict
4. **Schema migration issue** – DB migration didn't run or is incompatible
5. **Feature flag misconfiguration** – Flag enabled before code is ready

## Diagnosis Steps
1. Check deploy timeline: `kubectl rollout history deployment/<service>`
2. Diff the last two deployments for config changes
3. Review error logs for stack traces unique to the new version
4. Check database migration status
5. Verify feature flag states

## Remediation
- **Immediate**: Roll back to the previous known-good version
- **Short-term**: Fix the bug, add test coverage, re-deploy via canary
- **Long-term**: Implement canary deploys, add pre-deploy smoke tests

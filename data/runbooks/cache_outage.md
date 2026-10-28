# Cache Outage Runbook

## Symptoms
- `Redis connection refused` / `ECONNRESET` / `READONLY You can't write against a read only replica`
- Cache hit ratio collapses to zero
- Database load and latency jump as every request misses the cache
- `OOM command not allowed when used memory > maxmemory`

## Common Causes
1. **Cache node down or failing over** – Primary lost, replica promoting
2. **Memory limit reached** – No eviction policy, or keys without TTL
3. **Connection limit** – Client pools exceed `maxclients`
4. **Thundering herd** – A hot key expires and every request rebuilds it

## Diagnosis Steps
1. `redis-cli ping` and `INFO replication`, `INFO memory`, `INFO clients`
2. Check failover events in the managed cache console
3. Compare cache hit ratio with database query rate
4. Find hot keys with `--hotkeys` or keyspace notifications

## Remediation
- **Immediate**: Fail over or restart the cache node; protect the database with rate limits
- **Short-term**: Set maxmemory-policy and TTLs; cap client pool sizes
- **Long-term**: Request coalescing for hot keys, stale-while-revalidate, multi-AZ replicas

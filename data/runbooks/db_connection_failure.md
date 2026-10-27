# Database Connection Failure Runbook

## Symptoms
- `SQLSTATE 08006: connection_failure` in logs
- Connection pool exhaustion (0 available connections)
- Queries timing out after 30s
- Cascading failures across dependent services

## Common Causes
1. **Database overload** – Too many concurrent connections
2. **Network partition** – VPC or firewall misconfiguration
3. **Credential rotation** – Password changed but not updated in secrets
4. **Database maintenance** – Failover or version upgrade in progress
5. **Connection leak** – Application not returning connections to pool

## Diagnosis Steps
1. Test direct connectivity: `pg_isready -h <host> -p 5432`
2. Check connection count: `SELECT count(*) FROM pg_stat_activity;`
3. Review CloudSQL / RDS console for maintenance events
4. Check secrets manager for recent rotation
5. Review application connection pool metrics

## Remediation
- **Immediate**: Restart affected pods to reset connection pools
- **Short-term**: Increase max connections or add PgBouncer
- **Long-term**: Implement connection pooling, add retry with backoff, monitor pool usage

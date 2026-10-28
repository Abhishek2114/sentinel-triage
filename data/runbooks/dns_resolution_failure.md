# DNS Resolution Failure Runbook

## Symptoms
- `getaddrinfo ENOTFOUND` / `Name or service not known` errors
- `no such host` or `SERVFAIL` when calling internal services
- Intermittent failures that differ between pods or nodes
- Latency spikes on the first request to a host

## Common Causes
1. **Resolver overload** – CoreDNS / kube-dns pods saturated or crash-looping
2. **Record removed or renamed** – Service deleted, record TTL expired
3. **Search-path amplification** – ndots settings multiply lookups per request
4. **Upstream resolver outage** – Cloud DNS or forwarders unreachable

## Diagnosis Steps
1. Resolve from inside a pod: `nslookup <service>` and `dig <service>`
2. Check CoreDNS pod health, restarts and logs
3. Inspect `/etc/resolv.conf` for search domains and ndots
4. Compare results from different nodes

## Remediation
- **Immediate**: Restart or scale CoreDNS; point clients at a healthy resolver
- **Short-term**: Restore the missing record; lower ndots or use fully qualified names
- **Long-term**: Run NodeLocal DNSCache, alert on resolver error rate

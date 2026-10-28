# TLS Certificate Expiry Runbook

## Symptoms
- `x509: certificate has expired or is not yet valid` in client logs
- `SSL handshake failed` / `certificate verify failed` errors
- Sudden 100% failure on one upstream or ingress host while others work
- Browsers show "Your connection is not private"

## Common Causes
1. **Expired certificate** – Renewal job failed or was never configured
2. **Missing intermediate** – Server sends an incomplete chain after renewal
3. **Clock skew** – Host time wrong, so a valid certificate looks expired or not yet valid
4. **Hostname mismatch** – Certificate SANs don't cover the requested name

## Diagnosis Steps
1. Inspect the served chain: `openssl s_client -connect host:443 -servername host`
2. Check expiry: `openssl x509 -noout -dates`
3. Check cert-manager / ACME renewal logs and certificate resources
4. Verify system time with `timedatectl` or `chronyc tracking`

## Remediation
- **Immediate**: Renew or reissue the certificate and reload the server or ingress
- **Short-term**: Fix the renewal job; serve the full chain
- **Long-term**: Alert 21 days before expiry, automate renewal end to end

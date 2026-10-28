# Disk Full Runbook

## Symptoms
- `No space left on device` (ENOSPC) errors on write
- Log rotation or temp file creation failing
- Database refusing writes, WAL archiving stalled
- Node condition `DiskPressure`, pods evicted

## Common Causes
1. **Unrotated logs** – Application or container logs growing without limit
2. **Orphaned temp files** – Uploads or exports never cleaned up
3. **Volume sized too small** – Data growth outpaced the provisioned volume
4. **Retention misconfigured** – Old partitions, backups or snapshots kept forever

## Diagnosis Steps
1. Check usage: `df -h` and `df -i` (inodes can run out before bytes)
2. Find the largest paths: `du -xh / | sort -h | tail -20`
3. Check for deleted-but-open files: `lsof +L1`
4. Review log rotation config and retention jobs

## Remediation
- **Immediate**: Free space by removing rotated logs and temp files; restart processes holding deleted files
- **Short-term**: Expand the volume; enable log rotation with size limits
- **Long-term**: Alert at 80% usage, enforce retention policies, move bulk data to object storage

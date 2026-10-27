# Memory Leak Runbook

## Symptoms
- Gradually increasing memory usage over hours/days
- GC pause times increasing
- OOM kills by the container runtime
- Increasing swap usage (if swap is enabled)
- Performance degradation correlated with uptime

## Common Causes
1. **Unbounded cache** – In-memory cache without eviction policy
2. **Event listener leak** – Listeners registered but never removed
3. **Unclosed resources** – Database connections, file handles, HTTP clients
4. **Large object accumulation** – Growing data structures in long-running tasks
5. **Native memory leak** – C extension or JNI code leaking allocations

## Diagnosis Steps
1. Plot memory usage over time per pod
2. Take heap dumps at different time intervals and diff them
3. Check for growing object counts in specific classes/types
4. Review recent code changes for new caching or data structures
5. Run the application with memory profiling enabled

## Remediation
- **Immediate**: Restart affected pods to reclaim memory
- **Short-term**: Add memory limits and OOM handling, implement LRU caches
- **Long-term**: Fix the leak source, add memory usage monitoring and alerts

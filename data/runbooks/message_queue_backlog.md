# Message Queue Backlog Runbook

## Symptoms
- Consumer lag growing (Kafka) or queue depth rising (RabbitMQ / SQS)
- `consumer group rebalancing` repeated in logs
- Messages processed minutes or hours late; dead-letter queue filling
- Producers blocked or `queue full` / `buffer exhausted` errors

## Common Causes
1. **Slow consumers** – A downstream dependency made each message slower
2. **Poison message** – One message fails and is retried forever
3. **Too few consumers** – Partitions or replicas not scaled with traffic
4. **Rebalance loop** – Consumers exceed `max.poll.interval` and get kicked out

## Diagnosis Steps
1. Check lag per partition: `kafka-consumer-groups --describe`
2. Look for one message id failing repeatedly
3. Compare consume rate with produce rate
4. Check consumer processing time and poll interval settings

## Remediation
- **Immediate**: Move poison messages to the dead-letter queue; scale consumers
- **Short-term**: Raise max.poll.interval or batch smaller; add partitions
- **Long-term**: Alert on lag, make handlers idempotent, bound retries

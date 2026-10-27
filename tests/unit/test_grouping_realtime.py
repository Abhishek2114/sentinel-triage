"""Unit tests for incident grouping, its evaluation, and live event delivery."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api import ws
from app.detect.pipeline import FoundAnomaly
from app.eval.harness import evaluate_run
from app.eval.metrics import FaultLabel, IncidentReport, score_incidents
from app.main import app
from app.realtime import CHANNEL, publish, relay
from app.triage.grouping import group_anomalies, joins
from app.triage.service import TriageOutcome
from scripts.generate_logs import generate_logs

T0 = datetime(2024, 6, 15, tzinfo=UTC)
GAP = timedelta(minutes=15)


def _at(minute: float) -> datetime:
    return T0 + timedelta(minutes=minute)


def _anomaly(service: str, start: float, end: float) -> FoundAnomaly:
    return FoundAnomaly("error_burst", service, _at(start), _at(end), 1.0)


class TestJoins:
    def test_within_gap_after_and_before(self):
        assert joins(_at(0), _at(10), _at(24), _at(26), GAP)  # starts 14 min after
        assert joins(_at(30), _at(40), _at(10), _at(16), GAP)  # ends 14 min before

    def test_beyond_gap(self):
        assert not joins(_at(0), _at(10), _at(26), _at(30), GAP)


class TestGroupAnomalies:
    def test_flapping_fault_becomes_one_group(self):
        groups = group_anomalies(
            [_anomaly("db", 0, 3), _anomaly("db", 8, 10), _anomaly("db", 20, 22)], GAP
        )
        assert [len(g) for g in groups] == [3]  # each joins the widened span

    def test_services_and_distant_faults_stay_apart(self):
        groups = group_anomalies(
            [_anomaly("db", 0, 3), _anomaly("web", 1, 4), _anomaly("db", 60, 62)], GAP
        )
        assert sorted((g[0].service, len(g)) for g in groups) == [("db", 1), ("db", 1), ("web", 1)]

    def test_order_of_input_does_not_matter(self):
        a = [_anomaly("db", 20, 22), _anomaly("db", 0, 3), _anomaly("db", 8, 10)]
        assert [len(g) for g in group_anomalies(a, GAP)] == [3]


class TestScoreIncidents:
    def test_duplicates_merges_and_false_incidents(self):
        faults = [
            FaultLabel("cpu_spike", "db", _at(0), _at(10)),
            FaultLabel("bad_deploy", "db", _at(12), _at(20)),
        ]
        groups = [
            [_anomaly("db", 1, 2)],  # fault 1
            [_anomaly("db", 5, 6)],  # fault 1 again → duplicate
            [_anomaly("db", 9, 13)],  # spans both → merged
            [_anomaly("db", 50, 51)],  # nothing → false
        ]
        r = score_incidents("none", groups, faults)
        assert (r.incidents, r.faults_caught, r.duplicates, r.merged, r.false_incidents) == (
            4,
            2,
            2,  # fault 1 has 3 incidents, fault 2 has 1
            1,
            1,
        )

    def test_add_and_ratio(self):
        total = IncidentReport("15 min")
        total.add(IncidentReport("15 min", incidents=5, faults_caught=4, false_incidents=1))
        total.add(IncidentReport("15 min", incidents=3, faults_caught=2))
        assert total.per_caught_fault == (8 - 1) / 6
        assert total.to_dict()["incidents"] == 8


class TestTriageOutcomeEvent:
    @pytest.mark.parametrize(
        ("action", "event"),
        [("created", "incident.created"), ("grouped", "incident.updated"), ("existing", None)],
    )
    def test_event_per_action(self, action, event):
        assert TriageOutcome(1, action, "fallback").event == event


# ── Redis ─────────────────────────────────────────────


class TestPublish:
    async def test_reaches_subscribers(self):
        server = fakeredis.FakeServer()
        listener = fakeredis.FakeAsyncRedis(server=server)
        pubsub = listener.pubsub()
        await pubsub.subscribe(CHANNEL)
        await pubsub.get_message(timeout=1)  # subscribe confirmation

        ok = await publish(lambda: fakeredis.FakeAsyncRedis(server=server), {"type": "x"})

        message = await pubsub.get_message(timeout=1)
        assert ok and json.loads(message["data"]) == {"type": "x"}
        await pubsub.aclose()

    async def test_redis_down_is_logged_not_raised(self):
        server = fakeredis.FakeServer()
        server.connected = False
        ok = await publish(lambda: fakeredis.FakeAsyncRedis(server=server), {"type": "x"})
        assert ok is False


class TestRelay:
    async def test_forwards_messages_and_survives_a_dropped_connection(self):
        server = fakeredis.FakeServer()
        received: list[str] = []
        attempts = 0

        def factory():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise_on_subscribe = fakeredis.FakeAsyncRedis(server=server)
                pubsub = raise_on_subscribe.pubsub()

                async def boom(*_):
                    raise RedisConnectionError("connection reset")

                pubsub.subscribe = boom
                raise_on_subscribe.pubsub = lambda: pubsub
                return raise_on_subscribe
            return fakeredis.FakeAsyncRedis(server=server)

        async def deliver(payload: str) -> None:
            received.append(payload)

        task = asyncio.create_task(relay(factory, deliver, min_backoff=0.01))
        sender = fakeredis.FakeAsyncRedis(server=server)
        for _ in range(200):  # wait until the second attempt has subscribed
            if attempts >= 2 and (await sender.pubsub_numsub(CHANNEL))[0][1]:
                break
            await asyncio.sleep(0.01)
        await sender.publish(CHANNEL, '{"type": "incident.created"}')
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert attempts >= 2
        assert received == ['{"type": "incident.created"}']


# ── WebSocket ─────────────────────────────────────────


class TestWebSocket:
    def test_broadcast_reaches_connected_client(self):
        client = TestClient(app)  # no context manager: lifespan (and the relay) not started
        with client.websocket_connect("/api/v1/ws/alerts") as socket:
            assert len(ws._connections) == 1
            # Run broadcast on the app's event loop, where the socket lives
            socket.portal.call(ws.broadcast, '{"type": "incident.updated"}')
            assert socket.receive_text() == '{"type": "incident.updated"}'
        assert ws._connections == set()


class TestEvaluationReportsIncidents:
    def test_grouping_never_adds_incidents(self):
        lines, labels = generate_logs(seed=1003)
        none, grouped = evaluate_run(lines, labels, ["error_burst"]).incidents
        assert (none.grouping, grouped.grouping) == ("none", "15 min")
        assert grouped.incidents <= none.incidents
        assert grouped.duplicates == 0 and grouped.merged == 0

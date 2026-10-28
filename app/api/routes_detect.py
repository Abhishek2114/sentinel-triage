"""Detection endpoint – run anomaly detection over stored events on demand."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_key
from app.detect.pipeline import run_detection
from app.models.anomaly import Anomaly
from app.schemas import AnomalyRead, DetectRequest, DetectResponse

router = APIRouter(tags=["detect"])


@router.post(
    "/detect", response_model=DetectResponse, dependencies=[Depends(require_key)]
)
async def detect(
    body: DetectRequest,
    session: AsyncSession = Depends(get_session),
) -> DetectResponse:
    """Run detection now and return the anomalies found.

    Runs in the request, which is fine for hours of logs (well under a second
    for the 6-hour synthetic set). The scheduled Celery task does the same work
    in the background for live data.
    """
    summary = await run_detection(
        session,
        detectors=body.detectors,
        since=body.since,
        until=body.until,
        service=body.service,
        window_minutes=body.window_minutes,
        max_services=get_settings().max_services_per_detection,
    )

    anomalies: list[Anomaly] = []
    if summary.anomaly_ids:
        result = await session.execute(
            select(Anomaly)
            .where(Anomaly.id.in_(summary.anomaly_ids))
            .order_by(Anomaly.window_start, Anomaly.service, Anomaly.detector)
        )
        anomalies = list(result.scalars().all())

    return DetectResponse(
        services=summary.services,
        windows_scored=summary.windows_scored,
        new_anomalies=len(summary.new_anomaly_ids),
        anomalies=[AnomalyRead.model_validate(a) for a in anomalies],
    )

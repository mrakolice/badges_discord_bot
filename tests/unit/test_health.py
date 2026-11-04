import json

import pytest

from src.health import HealthServer


@pytest.mark.parametrize(
    ("checks", "status"),
    [({"discord": True, "checks": True}, 200), ({"discord": False, "checks": True}, 503)],
)
async def test_health_status(checks: dict[str, bool], status: int) -> None:
    server = HealthServer(0, lambda: checks)
    response = await server._health(None)  # type: ignore[arg-type]
    assert response.status == status
    body = json.loads(response.body)  # type: ignore[arg-type]
    assert body["status"] == ("ok" if status == 200 else "unhealthy")

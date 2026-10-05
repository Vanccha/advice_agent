import os

import httpx
import pytest


@pytest.fixture(scope="session")
def core_api_base_url() -> str:
    return os.environ.get("CORE_API_BASE_URL", "http://core-api:8000")


@pytest.fixture(scope="session")
def core_api_reachable(core_api_base_url: str) -> bool:
    try:
        resp = httpx.get(f"{core_api_base_url}/health", timeout=2.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_core_api_unreachable(request: pytest.FixtureRequest, core_api_reachable: bool) -> None:
    if "needs_core_api" in request.keywords and not core_api_reachable:
        pytest.skip("core-api not reachable from this environment")

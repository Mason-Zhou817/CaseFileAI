from fastapi.testclient import TestClient

from casefile_ai.interfaces.api.main import create_app


def test_browser_shell_and_assets_are_served_without_backend_configuration() -> None:
    with TestClient(create_app(case_service=object())) as client:  # type: ignore[arg-type]
        page = client.get("/")
        script = client.get("/assets/workspace.js")
        style = client.get("/assets/app.css")

    assert page.status_code == 200
    assert "Claim 与 Coverage" in page.text
    assert "Timeline" in page.text
    assert script.status_code == 200 and "loadCases" in script.text
    assert style.status_code == 200 and "text/css" in style.headers["content-type"]

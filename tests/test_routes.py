import re

from starlette.testclient import TestClient


def test_health_is_public_and_does_not_touch_db(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_sets_httponly_session_cookie(client):
    page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match and match.group(1) == client.cookies["algo_csrf"]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "pw", "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/").status_code == 200


def test_login_without_csrf_is_403(client):
    response = client.post("/login", data={"username": "admin", "password": "pw"})
    assert response.status_code == 403


def test_wrong_password_is_generic(client):
    csrf = client.get("/login").cookies["algo_csrf"]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "bad", "csrf_token": csrf},
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_logout_clears_session(client: TestClient, login):
    login()
    csrf = client.cookies["algo_csrf"]
    response = client.post(
        "/logout", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_database_failure_returns_503(failing_client):
    failing_client.cookies.set("algo_session", "bogus")
    response = failing_client.get("/")
    assert response.status_code == 503
    assert "unavailable" in response.text.lower()


def test_dashboard_reissues_valid_csrf(client, login):
    login()
    client.cookies.delete("algo_csrf")
    page = client.get("/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match
    assert match.group(1) == client.cookies["algo_csrf"]
    response = client.post(
        "/logout", data={"csrf_token": match.group(1)}, follow_redirects=False
    )
    assert response.status_code == 303


def test_dashboard_shows_configured_trading_mode(settings, login):
    from tests.fakes import build_test_app

    live = settings.model_copy(update={"trading_mode": "live"})
    with TestClient(build_test_app(live)) as live_client:
        page = live_client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        live_client.post(
            "/login",
            data={"username": "admin", "password": "pw", "csrf_token": token},
        )
        dashboard = live_client.get("/")
        assert ">live<" in dashboard.text


def test_login_throttle_uses_settings(settings):
    from tests.fakes import build_test_app

    limited = settings.model_copy(update={"login_max_attempts": 2})
    application = build_test_app(limited)
    assert application.state.auth.throttle.max_attempts == 2


def test_dashboard_caches_upstox_status(client, login):
    login()
    calls = {"count": 0}

    def counted_status():
        calls["count"] += 1
        return "connected"

    client.app.state.upstox_auth.status = counted_status
    client.get("/")
    client.get("/")
    assert calls["count"] == 1

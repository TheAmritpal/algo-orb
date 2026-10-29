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
    csrf = client.get("/login").cookies["algo_csrf"]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "pw", "csrf_token": csrf},
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

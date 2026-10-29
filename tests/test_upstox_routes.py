def test_callback_sets_no_token_in_url_or_body(client, login):
    login()
    response = client.get("/auth/upstox/login", follow_redirects=False)
    assert response.status_code == 303
    state_cookie = client.cookies["algo_upstox_state"]
    response = client.get(
        f"/auth/upstox/callback?code=abc&state={state_cookie}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "abc" not in response.headers["location"]
    assert "token" not in response.text.lower()


def test_callback_rejects_state_not_matching_cookie(client, login):
    login()
    client.cookies.set("algo_upstox_state", "cookie-state")
    response = client.get(
        "/auth/upstox/callback?code=abc&state=other",
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_callback_rejects_missing_state_cookie(client, login):
    login()
    response = client.get(
        "/auth/upstox/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_callback_replay_is_rejected(client, login):
    login()
    client.get("/auth/upstox/login", follow_redirects=False)
    state = client.cookies["algo_upstox_state"]
    first = client.get(
        f"/auth/upstox/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = client.get(
        f"/auth/upstox/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert second.status_code == 400


def test_status_requires_login(client):
    response = client.get("/auth/upstox/status")
    assert response.status_code == 401

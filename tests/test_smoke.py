def test_read_main(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Login" in response.content or b"Smart Building" in response.content

def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Login" in response.content

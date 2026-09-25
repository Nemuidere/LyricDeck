LYRICS = "Я иду домой, и ветер поёт.\nТы не знаешь, что-то стало иначе."


def add(client, **overrides):
    data = {"artist": "Test Artist", "title": "Test Song", "lyrics": LYRICS} | overrides
    return client.post("/songs/new", data=data)


def test_empty_list(client):
    assert "No songs yet" in client.get("/").get_data(as_text=True)


def test_add_and_list(client):
    assert add(client).status_code == 302
    page = client.get("/").get_data(as_text=True)
    assert "Test Song" in page and "Test Artist" in page and "2 lines" in page


def test_add_requires_title_and_lyrics(client):
    page = add(client, title="").get_data(as_text=True)
    assert "Title and lyrics are required." in page
    assert "No songs yet" in client.get("/").get_data(as_text=True)


def test_edit(client):
    add(client)
    assert "поёт" in client.get("/songs/1/edit").get_data(as_text=True)
    client.post("/songs/1/edit", data={"artist": "A", "title": "Renamed", "lyrics": "Одна строка"})
    page = client.get("/").get_data(as_text=True)
    assert "Renamed" in page and "1 line " in page


def test_delete(client):
    add(client)
    client.post("/songs/1/delete")
    assert "No songs yet" in client.get("/").get_data(as_text=True)


def test_missing_song_404(client):
    assert client.get("/songs/99/edit").status_code == 404
    assert client.post("/songs/99/delete").status_code == 404

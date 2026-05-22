import sqlite3


def test_categories_describe_all_upload_buckets(client):
    response = client.get("/api/wiki/categories")

    assert response.status_code == 200
    body = response.json()
    assert body["all"] == ["articles", "clippings", "notes", "images", "pdfs"]
    assert body["text"] == ["articles", "clippings", "notes"]
    assert ".md" in body["extensions"]["articles"]
    assert ".png" in body["extensions"]["images"]
    assert body["extensions"]["pdfs"] == [".pdf"]


def test_upload_text_file_sanitizes_name_writes_file_and_records_metadata(client):
    response = client.post(
        "/api/wiki/upload",
        data={"category": "articles"},
        files={"file": ("folder/My Article!.MD", b"# hello\n", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "My_Article.md",
        "category": "articles",
        "size": 8,
    }
    assert (client.raw_dir / "articles" / "My_Article.md").read_bytes() == b"# hello\n"

    list_response = client.get("/api/wiki/list")
    assert list_response.status_code == 200
    rows = list_response.json()
    assert len(rows) == 1
    assert rows[0]["category"] == "articles"
    assert rows[0]["filename"] == "My_Article.md"
    assert rows[0]["original"] == "folder/My Article!.MD"
    assert rows[0]["size_bytes"] == 8
    assert rows[0]["uploaded_at"]


def test_upload_images_and_pdfs_are_auto_routed_regardless_of_category(client):
    image_response = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("diagram.PNG", b"image", "image/png")},
    )
    pdf_response = client.post(
        "/api/wiki/upload",
        data={"category": "articles"},
        files={"file": ("paper.PDF", b"%PDF", "application/pdf")},
    )

    assert image_response.status_code == 200
    assert image_response.json()["category"] == "images"
    assert (client.raw_dir / "images" / "diagram.png").read_bytes() == b"image"
    assert pdf_response.status_code == 200
    assert pdf_response.json()["category"] == "pdfs"
    assert (client.raw_dir / "pdfs" / "paper.pdf").read_bytes() == b"%PDF"


def test_upload_rejects_text_without_valid_category_or_extension(client):
    missing_category = client.post(
        "/api/wiki/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    bad_extension = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("page.html", b"<p>x</p>", "text/html")},
    )

    assert missing_category.status_code == 400
    assert "must select a category" in missing_category.json()["detail"]
    assert bad_extension.status_code == 400
    assert "Extension .html is not allowed" in bad_extension.json()["detail"]


def test_upload_rejects_empty_duplicate_and_extensionless_files(client):
    empty = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("note.md", b"", "text/markdown")},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "Empty file"

    no_extension = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("README", b"text", "text/plain")},
    )
    assert no_extension.status_code == 400
    assert no_extension.json()["detail"] == "Filename must have an extension"

    first = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("same.md", b"first", "text/markdown")},
    )
    duplicate = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("same.md", b"second", "text/markdown")},
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert (client.raw_dir / "notes" / "same.md").read_bytes() == b"first"

    with sqlite3.connect(client.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM wikis WHERE category = 'notes' AND filename = 'same.md'"
        ).fetchone()[0]
    assert count == 1


def test_upload_rejects_files_larger_than_configured_limit(client, monkeypatch):
    from app.routes import wiki

    monkeypatch.setattr(wiki, "MAX_FILE_SIZE", 3)

    response = client.post(
        "/api/wiki/upload",
        data={"category": "notes"},
        files={"file": ("large.md", b"1234", "text/markdown")},
    )

    assert response.status_code == 413
    assert "File exceeds 3 bytes" == response.json()["detail"]
    assert not (client.raw_dir / "notes" / "large.md").exists()


def test_delete_removes_file_and_metadata(client):
    upload = client.post(
        "/api/wiki/upload",
        data={"category": "clippings"},
        files={"file": ("clip.txt", b"saved", "text/plain")},
    )
    assert upload.status_code == 200
    assert (client.raw_dir / "clippings" / "clip.txt").exists()

    response = client.delete("/api/wiki/clippings/clip.txt")

    assert response.status_code == 200
    assert response.json() == {"deleted": "clippings/clip.txt"}
    assert not (client.raw_dir / "clippings" / "clip.txt").exists()
    assert client.get("/api/wiki/list").json() == []


def test_delete_rejects_unknown_category(client):
    response = client.delete("/api/wiki/unknown/file.md")

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown category"

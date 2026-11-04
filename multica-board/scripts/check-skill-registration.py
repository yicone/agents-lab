#!/usr/bin/env python3
"""Check real local/shared/Grok registration through an isolated HTTP server.

Reads this checkout and local skill entries. Uses an empty temporary Workspace
cache and disables upstream upgrade fetching; never touches a running Board.
"""
import http.client
from collections import Counter
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from multica_board.server import BoardServer
from multica_board.skills_view import build_payload
from multica_board.store import Store
from multica_board.views import Views


def main():
    document = build_payload(upgrade_fetch=lambda _url: (0, None, "offline verification"))
    assert document["ok"], "inventory could not be read"
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "cache.sqlite")
        views = Views(store, "registration-check", app_url="https://example.invalid", collect_interval_s=60)
        server = BoardServer("127.0.0.1", 0, views)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            with patch("multica_board.skills_view.skills_document", return_value=document):
                connection = http.client.HTTPConnection(*server.server_address, timeout=30)
                connection.request("GET", "/api/skills")
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
            assert response.status == 200
            rows = result["items"]
            fields = ("id", "canonical_path", "body_sha256", "source_class", "loaders")
            assert all(set(fields) <= row.keys() for row in rows)
            local = next(row for row in rows if row.get("body_sha256") and row["origin"] != "grok_bot")
            shared = next(row for row in rows if row["id"] != local["id"] and row.get("body_sha256") and any(
                loader["mark"] == "shared" for loader in row["loaders"]))
            grok = next(row for row in rows if row.get("source_class") == "grok_bot")
            assert grok["id"] == grok["key"] and grok["body_sha256"] is None
            assert grok["body_hash_state"] == "not_applicable"
            for row in rows:
                for loader in row["loaders"]:
                    if loader["load_state"] == "unverified":
                        assert loader["runtime_loaded"] is None
            for label, row in (("local", local), ("shared", shared), ("grok", grok)):
                sample = {key: row[key] for key in (*fields, "body_hash_state")}
                sample["loaders"] = [loader for loader in sample["loaders"] if loader["mark"] in
                                     ("shared", "direct", "declared")]
                print(json.dumps({"sample": label, **sample}, ensure_ascii=False))
            print(json.dumps({"status": response.status, "rows_checked": len(rows),
                              "body_hash_states": dict(Counter(row["body_hash_state"] for row in rows)),
                              "result": "PASS"}))
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
            store.close()


if __name__ == "__main__":
    main()

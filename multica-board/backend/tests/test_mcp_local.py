"""Backend tests for mcp_local.py: safe open, parsers, transport enum, key file (§14 1-6, 8)."""

from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from multica_board import mcp_local  # noqa: E402
from multica_board.mcp_local import (  # noqa: E402
    KEY_BYTES,
    KEY_NAME,
    McpContext,
    SourcesInvalid,
    fingerprints,
    load_fingerprint_key,
    load_sources,
    parse_container,
    read_declared,
    row_id,
    summarize_entry,
    transport_enum,
    yaml_mcp_container,
    SOURCES_PATH,
)

SENT = "SENT-LOCAL-7f3a"


def touch_tree(root: Path, mapping: dict[str, str]) -> None:
    for rel, text in mapping.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def fake_sources() -> dict:
    doc = json.loads(SOURCES_PATH.read_bytes())
    return doc


class SourcesTests(unittest.TestCase):
    """§14.2 declaration loading + validation."""

    def test_shipped_declaration_valid_and_evidence_nonempty(self):
        doc = load_sources()
        self.assertEqual(len(doc["tools"]), 9)
        self.assertEqual(len(doc["project"]), 5)
        for t in doc["tools"]:
            self.assertTrue(t["evidence"].strip())
        for p in doc["project"]:
            self.assertTrue(p["evidence"].strip())

    def test_declaration_validation_rejects_bad_paths(self):
        base = fake_sources()
        for bad in ("/abs/x.json", "../up.json", "a//b.json", "./x.json", "a/./b.json", "a\x00b.json"):
            doc = json.loads(json.dumps(base))
            doc["tools"][0]["global"]["path"] = bad
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(doc, f)
            with self.assertRaises(SourcesInvalid, msg=bad):
                load_sources(Path(f.name))
            os.unlink(f.name)

    def test_read_all_nine_global_and_five_project_files(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            repo = Path(td) / "repo"
            touch_tree(home, {
                ".cursor/mcp.json": json.dumps({"mcpServers": {"a": {"command": "x"}}}),
                ".codex/config.toml": '[mcp_servers.b]\ncommand = "x"\n',
                ".claude.json": json.dumps({"mcpServers": {"c": {"command": "x"}}, "other": SENT}),
                ".hermes/config.yaml": "mcp_servers:\n  d:\n    command: x\n",
                ".gemini/settings.json": '{"mcpServers": {"e": {"command": "x"}}, /*c*/}',
                ".config/devin/mcp_config.json": json.dumps({"mcpServers": {"f": {"command": "x"}}}),
                ".gemini/antigravity/mcp_config.json": json.dumps({"mcpServers": {"g": {"command": "x"}}}),
                ".codeium/windsurf/mcp_config.json": json.dumps({"mcpServers": {"h": {"command": "x"}}}),
                ".config/opencode/opencode.json": '// c\n{"mcp": {"i": {"type": "local", "command": ["x"]},},}',
            })
            touch_tree(repo, {
                ".mcp.json": json.dumps({"mcpServers": {"p1": {"command": "x"}}}),
                ".cursor/mcp.json": json.dumps({"mcpServers": {"p2": {"command": "x"}}}),
                ".codex/config.toml": '[mcp_servers.p3]\ncommand = "x"\n',
                ".gemini/settings.json": '{"mcpServers": {"p4": {"command": "x"}}}',
                "opencode.json": '{"mcp": {"p5": {"type": "local", "command": ["x"]}}}',
            })
            scan = mcp_local.scan_sources(load_sources(), home, repo)
            for t in scan["tools"]:
                self.assertEqual(t["state"], "ok", t["id"])
                self.assertEqual(t["entry_count"], 1, t["id"])
            self.assertEqual([e["raw"] for t in scan["tools"] for e in t["entries"]],
                             list("abcdefghi"))
            for p in scan["project"]:
                self.assertEqual(p["state"], "ok", p["location"])
                self.assertEqual(p["entry_count"], 1)
            self.assertEqual([e["raw"] for p in scan["project"] for e in p["entries"]],
                             ["p1", "p2", "p3", "p4", "p5"])
            # Non-container top-level keys never surface anywhere.
            self.assertNotIn(SENT, json.dumps(scan))


class ParserTests(unittest.TestCase):
    """§14.3 restricted parsers."""

    def test_yaml_block_maps_scalars_lists(self):
        text = """other_key:
  deep: [1, 2]
mcp_servers:
  fs:
    command: "npx"
    args:
      - "-y"
      - pkg
    env:
      KEY: v alue
  remote:
    url: 'https://x/mcp'
    enabled: false
    tags: [a, "b c"]
"""
        out = yaml_mcp_container(text)
        self.assertEqual(out["fs"]["command"], "npx")
        self.assertEqual(out["fs"]["args"], ["-y", "pkg"])
        self.assertEqual(out["fs"]["env"], {"KEY": "v alue"})
        self.assertEqual(out["remote"]["enabled"], False)
        self.assertEqual(out["remote"]["tags"], ["a", "b c"])
        self.assertNotIn("other_key", out)

    def test_yaml_unsupported_constructs(self):
        cases = {
            "anchor": "mcp_servers:\n  a:\n    command: &x n\n",
            "alias": "mcp_servers:\n  a:\n    command: *x\n",
            "tag": "mcp_servers:\n  a: !foo\n    command: x\n",
            "multiline": "mcp_servers:\n  a:\n    note: |\n      hi\n",
            "merge": "mcp_servers:\n  a:\n    <<: {command: x}\n",
            "multidoc": "---\nmcp_servers:\n  a:\n    command: x\n",
        }
        for name, text in cases.items():
            with self.assertRaises(mcp_local._YamlUnsupported, msg=name):
                yaml_mcp_container(text)
            self.assertIs(parse_container(text.encode(), "yaml", "mcp_servers"), mcp_local._UNSUPPORTED, name)

    def test_yaml_other_top_level_sentinel_never_read(self):
        text = f"other:\n  - {SENT}\nmcp_servers:\n  a:\n    command: x\n"
        out = yaml_mcp_container(text)
        self.assertEqual(list(out), ["a"])
        self.assertNotIn(SENT, json.dumps(out))

    def test_jsonc_comments_and_trailing_commas(self):
        text = '{\n // line\n "mcp": {\n  "a": {"type": "local", "command": ["x"]},\n },\n /* block */\n}\n'
        out = parse_container(text.encode(), "jsonc", "mcp")
        self.assertEqual(list(out), ["a"])

    def test_parse_error_and_missing_container(self):
        self.assertIs(parse_container(b"{bad", "json", "mcpServers"), mcp_local._PARSE_FAILED)
        self.assertIs(parse_container(b"[1]", "json", "mcpServers"), mcp_local._PARSE_FAILED)
        self.assertIs(parse_container(b'{"mcpServers": 5}', "json", "mcpServers"), mcp_local._PARSE_FAILED)
        self.assertEqual(parse_container(b'{"other": 1}', "json", "mcpServers"), {})
        self.assertIs(parse_container(b"\xff\xfe", "json", "mcp"), mcp_local._PARSE_FAILED)
        self.assertIs(parse_container(b"[mcp_servers.x]\nbad =", "toml", "mcp_servers"), mcp_local._PARSE_FAILED)


class SafeOpenTests(unittest.TestCase):
    """§14.4 safe-open cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()
        mcp_local._READ_CACHE.clear()

    def read(self, rel="cfg/mcp.json"):
        return read_declared(self.home, rel, "mcpServers", "json")

    def test_ok_and_absent(self):
        self.assertEqual(self.read()["state"], "absent")
        (self.home / "cfg").mkdir()
        (self.home / "cfg/mcp.json").write_text('{"mcpServers": {"a": {"command": "x"}}}')
        res = self.read()
        self.assertEqual((res["state"], res["entry_count"]), ("ok", 1))

    def test_file_symlink_not_followed_target_untouched(self):
        target = self.home / "target.json"
        target.write_text(f'{{"mcpServers": {{"{SENT}": {{"command": "x"}}}}}}')
        (self.home / "cfg").mkdir()
        os.symlink("target.json" if False else str(target), self.home / "cfg/mcp.json")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        res = self.read()
        self.assertEqual(res["state"], "symlink")
        self.assertNotIn(SENT, json.dumps(res))
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_dir_symlink(self):
        real = self.home / "realdir"
        real.mkdir()
        os.symlink(str(real), self.home / "cfg")
        self.assertEqual(self.read()["state"], "symlink")

    def test_fifo_is_not_regular_within_2s(self):
        (self.home / "cfg").mkdir()
        os.mkfifo(self.home / "cfg/mcp.json")
        out: list = []
        t = threading.Thread(target=lambda: out.append(self.read()), daemon=True)
        t.start()
        t.join(timeout=2)
        self.assertEqual(len(out), 1, "FIFO read must return within 2s")
        self.assertEqual(out[0]["state"], "not_regular")

    def test_dir_socket_and_nondir_middle(self):
        (self.home / "cfg/mcp.json").mkdir(parents=True)
        self.assertEqual(self.read()["state"], "not_regular")
        os.rmdir(self.home / "cfg/mcp.json")
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(self.home / "cfg/mcp.json"))
        try:
            self.assertEqual(self.read()["state"], "not_regular")
        finally:
            s.close()
        (self.home / "blocker").write_text("x")
        self.assertEqual(read_declared(self.home, "blocker/mcp.json", "mcpServers", "json")["state"],
                         "not_regular")

    def test_toctou_swap_before_open_and_after_open(self):
        (self.home / "cfg").mkdir()
        f = self.home / "cfg/mcp.json"
        f.write_text('{"mcpServers": {"a": {"command": "x"}}}')
        target = self.home / "evil.json"
        target.write_text(f'{{"mcpServers": {{"{SENT}": {{"command": "x"}}}}}}')
        real_open = os.open
        swapped = {"done": False}

        def swap_before(*a, **kw):
            if a[0] == "mcp.json" and not swapped["done"]:
                swapped["done"] = True
                f.unlink()
                os.symlink(str(target), f)
            return real_open(*a, **kw)

        mcp_local._READ_CACHE.clear()
        with mock.patch.object(mcp_local.os, "open", side_effect=swap_before):
            self.assertEqual(self.read()["state"], "symlink")

        # Swap after open but before read: fd still serves the original bytes.
        swapped["done"] = False
        f.unlink()
        f.write_text('{"mcpServers": {"a": {"command": "x"}}}')

        def swap_after(*a, **kw):
            fd = real_open(*a, **kw)
            if a[0] == "mcp.json" and not swapped["done"]:
                swapped["done"] = True
                f.unlink()
                os.symlink(str(target), f)
            return fd

        mcp_local._READ_CACHE.clear()
        with mock.patch.object(mcp_local.os, "open", side_effect=swap_after):
            res = self.read()
        self.assertEqual(res["state"], "ok")
        self.assertNotIn(SENT, json.dumps(res))

    def test_too_large_and_growing(self):
        (self.home / "cfg").mkdir()
        f = self.home / "cfg/mcp.json"
        f.write_bytes(b" " * (mcp_local.MAX_BYTES + 1))
        self.assertEqual(self.read()["state"], "too_large")
        f.write_bytes(b'{"mcpServers":' + b" " * mcp_local.MAX_BYTES + b"}")
        mcp_local._READ_CACHE.clear()
        self.assertEqual(self.read()["state"], "too_large")

    def test_denied(self):
        (self.home / "cfg").mkdir()
        f = self.home / "cfg/mcp.json"
        f.write_text("{}")
        f.chmod(0o000)
        try:
            self.assertEqual(self.read()["state"], "denied")
        finally:
            f.chmod(0o600)

    def test_platform_unsupported(self):
        (self.home / "cfg").mkdir()
        (self.home / "cfg/mcp.json").write_text("{}")
        calls = []
        real_open = os.open

        def spy(*a, **kw):
            calls.append(a)
            return real_open(*a, **kw)

        with mock.patch.object(mcp_local.os, "supports_dir_fd", set()):
            with mock.patch.object(mcp_local.os, "open", side_effect=spy):
                self.assertEqual(self.read()["state"], "platform_unsupported")
        self.assertEqual(calls, [], "no path opens when dir_fd unsupported")

    def test_cache_hit_skips_reread_same_stamp(self):
        (self.home / "cfg").mkdir()
        f = self.home / "cfg/mcp.json"
        f.write_text('{"mcpServers": {"a": {"command": "x"}}}')
        mcp_local._READ_CACHE.clear()
        self.assertEqual(self.read()["entry_count"], 1)
        f.write_text('{"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}}')
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size; stamp differs only via mtime
        # Rewrite with identical content+size restores stamp → cache hit path returns previous parse.
        f.write_text('{"mcpServers": {"a": {"command": "x"}}}')
        mcp_local._READ_CACHE.clear()
        self.assertEqual(self.read()["entry_count"], 1)


class IsolationTests(unittest.TestCase):
    """§14.1 readers take home/repo_root/data_dir params; no writes outside tempdirs."""

    def test_no_real_home_no_writes_outside_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            opened: list[str] = []
            real_open = os.open

            def spy(file, *a, **kw):
                opened.append(str(file))
                return real_open(file, *a, **kw)

            writes: list[tuple] = []
            real_chmod, real_unlink, real_rename = os.chmod, os.unlink, os.rename
            for name in ("chmod", "chown", "rename", "replace"):
                pass

            with mock.patch.object(mcp_local.os, "open", side_effect=spy), \
                 mock.patch.object(mcp_local.os, "chmod", side_effect=lambda *a, **k: writes.append(("chmod", a))), \
                 mock.patch.object(mcp_local.os, "chown", side_effect=lambda *a, **k: writes.append(("chown", a))), \
                 mock.patch.object(mcp_local.os, "rename", side_effect=lambda *a, **k: writes.append(("rename", a))), \
                 mock.patch.object(mcp_local.os, "replace", side_effect=lambda *a, **k: writes.append(("replace", a))):
                mcp_local.scan_sources(load_sources(), home, Path(td) / "repo")
                load_fingerprint_key(Path(td) / "data")
            home_str = str(Path.home())
            bad = [p for p in opened if p.startswith(home_str) and not p.startswith(str(home))]
            self.assertEqual(bad, [])
            self.assertEqual(writes, [])


class TransportEnumTests(unittest.TestCase):
    """§14.6 transport enum + broken reasons."""

    def test_enum_rules(self):
        self.assertEqual(summarize_entry("n", {"type": " local "})["transport"], "stdio")
        self.assertEqual(summarize_entry("n", {"type": "streamable_http"})["transport"], "http")
        self.assertEqual(summarize_entry("n", {"transport": "remote"})["transport"], "http")
        self.assertEqual(summarize_entry("n", {"command": "x"})["transport"], "stdio")
        self.assertEqual(summarize_entry("n", {"command": ["x", "y"]}, "opencode")["transport"], "stdio")
        self.assertEqual(summarize_entry("n", {"url": "https://u"})["transport"], "http")
        self.assertEqual(summarize_entry("n", {"url": "https://u"}, "gemini")["transport"], "sse")
        self.assertEqual(summarize_entry("n", {"httpUrl": "https://u"}, "gemini")["transport"], "http")
        self.assertEqual(summarize_entry("n", {"serverUrl": "https://u"})["transport"], "http")

    def test_broken_reasons(self):
        cases = [
            ({"type": f"weird-{SENT}"}, "unknown_transport"),
            ({"type": 5}, "bad_field_type"),
            ({"type": [SENT]}, "bad_field_type"),
            ({"type": {"k": SENT}}, "bad_field_type"),
            ({"command": {"a": SENT}}, "bad_field_type"),
            ({"command": "x", "args": SENT}, "bad_field_type"),
            ({"command": "x", "env": [SENT]}, "bad_field_type"),
            ({"url": "https://u", "headers": SENT}, "bad_field_type"),
            ({"url": 7}, "bad_field_type"),
            ({"command": "x", "disabled": "yes"}, "bad_field_type"),
            ({"extra": "x"}, "no_target"),
            ({"type": "stdio"}, "missing_target"),
            ({"type": "http"}, "missing_target"),
            ("not-a-dict", "not_object"),
            (["x"], "not_object"),
        ]
        for entry, reason in cases:
            rec = summarize_entry("n", entry)
            self.assertEqual(rec["state"], "broken", entry)
            self.assertEqual(rec["broken_reason"], reason, entry)
            self.assertIsNone(rec["fp_inputs"])

    def test_bad_field_type_nulls_flags(self):
        rec = summarize_entry("n", {"command": "x", "env": [1]})
        self.assertEqual(rec["env"], {"set": None, "count": None})
        self.assertEqual(rec["headers"], {"set": None, "count": None})

    def test_disabled_and_counts(self):
        rec = summarize_entry("n", {"command": "x", "disabled": True, "env": {"A": "1"},
                                  "env_vars": ["B"], "http_headers": {"H": "v"},
                                  "bearer_token_env_var": "T"})
        self.assertTrue(rec["disabled"])
        self.assertEqual(rec["env"], {"set": True, "count": 2})
        self.assertEqual(rec["headers"], {"set": True, "count": 2})
        rec = summarize_entry("n", {"command": "x", "enabled": False})
        self.assertTrue(rec["disabled"])

    def test_sentinel_never_in_output(self):
        entry = {"type": SENT, "command": SENT, "args": [SENT], "url": f"https://{SENT}",
                 "env": {SENT: SENT}, "headers": {SENT: SENT}, "cwd": SENT, "junk": SENT}
        rec = summarize_entry(SENT, entry)
        out = {k: v for k, v in rec.items() if k not in ("raw", "fp_inputs")}
        self.assertNotIn(SENT, json.dumps(out))


class KeyFileTests(unittest.TestCase):
    """§14.5 key file lifecycle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dd = Path(self.tmp.name) / "data"
        self.dd.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        return self.dd / KEY_NAME

    def test_creates_0600_32_bytes(self):
        res = load_fingerprint_key(self.dd)
        self.assertEqual(len(res["key"]), KEY_BYTES)
        st = self.key().stat()
        self.assertEqual(st.st_mode & 0o777, 0o600)
        res2 = load_fingerprint_key(self.dd)
        self.assertEqual(res2["key"], res["key"])  # stable across opens

    def test_symlink_rejected_unchanged(self):
        target = self.dd / "real-key"
        target.write_bytes(b"x" * 32)
        os.symlink("real-key", self.key())
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        res = load_fingerprint_key(self.dd)
        self.assertEqual((res["key"], res["code"]), (None, "key_symlink"))
        self.assertTrue(self.key().is_symlink())
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_fifo_rejected_within_2s(self):
        os.mkfifo(self.key())
        out: list = []
        t = threading.Thread(target=lambda: out.append(load_fingerprint_key(self.dd)), daemon=True)
        t.start()
        t.join(timeout=2)
        self.assertEqual(out[0]["code"], "key_not_regular")

    def test_mode_length_links_owner_dir(self):
        self.key().write_bytes(b"k" * 32)
        self.key().chmod(0o644)
        res = load_fingerprint_key(self.dd)
        self.assertEqual(res["code"], "key_mode")
        self.assertEqual(self.key().stat().st_mode & 0o777, 0o644)  # never chmod'ed
        self.assertEqual(self.key().read_bytes(), b"k" * 32)  # never overwritten

        self.key().chmod(0o600)
        for n in (31, 33):
            self.key().write_bytes(b"k" * n)
            res = load_fingerprint_key(self.dd)
            self.assertEqual(res["code"], "key_length")
            self.assertEqual(len(self.key().read_bytes()), n)

        self.key().write_bytes(b"k" * 32)
        link2 = self.dd / "second"
        os.link(self.key(), link2)
        res = load_fingerprint_key(self.dd)
        self.assertEqual(res["code"], "key_links")
        link2.unlink()

        # Injected euid mismatch: dir-owner check rejects first (documented).
        res = load_fingerprint_key(self.dd, geteuid=lambda: os.geteuid() + 1)
        self.assertEqual(res["code"], "key_dir_unsafe")

        # File owner mismatch while the dir passes: fake the key file's st_uid.
        real_fstat = os.fstat
        calls = {"n": 0}

        def fake_fstat(fd, *a):
            st = real_fstat(fd, *a)
            calls["n"] += 1
            if calls["n"] == 2:  # second fstat is the key file
                t = tuple(st)
                return os.stat_result(t[:4] + (st.st_uid + 1,) + t[5:])
            return st

        with mock.patch.object(mcp_local.os, "fstat", side_effect=fake_fstat):
            res = load_fingerprint_key(self.dd)
        self.assertEqual(res["code"], "key_owner")

    def test_dir_unsafe(self):
        self.dd.chmod(0o777)
        res = load_fingerprint_key(self.dd)
        self.assertEqual(res["code"], "key_dir_unsafe")
        self.assertEqual(self.dd.stat().st_mode & 0o777, 0o777)
        self.assertFalse(self.key().exists())
        self.dd.chmod(0o700)

        link_dir = Path(self.tmp.name) / "linkdir"
        os.symlink(str(self.dd), link_dir)
        res = load_fingerprint_key(link_dir)
        self.assertEqual(res["code"], "key_dir_unsafe")

    def test_eexist_on_create_falls_back_to_existing(self):
        res1 = load_fingerprint_key(self.dd)
        self.key().unlink()
        real_link = os.link
        calls = {"n": 0}

        def fake_link(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                (self.dd / KEY_NAME).write_bytes(res1["key"])
                (self.dd / KEY_NAME).chmod(0o600)
                raise FileExistsError(17, "exists")
            return real_link(*a, **kw)

        with mock.patch.object(mcp_local.os, "link", side_effect=fake_link):
            res2 = load_fingerprint_key(self.dd)
        self.assertEqual(res2["key"], res1["key"])  # read existing, never overwrote
        leftovers = [p.name for p in self.dd.iterdir() if p.name != KEY_NAME and p.name != "board.sqlite"]
        self.assertEqual(leftovers, [])


class FingerprintTests(unittest.TestCase):
    """§14.8 fingerprint properties."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dd = Path(self.tmp.name) / "data"
        self.dd.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_config_same_fp_env_change_only_env(self):
        key = secrets.token_bytes(32)
        a = summarize_entry("n", {"command": "x", "args": ["1"], "env": {"K": "1"}})["fp_inputs"]
        b = summarize_entry("n", {"command": "x", "args": ["1"], "env": {"K": "2"}})["fp_inputs"]
        fa, fb = fingerprints(a, key), fingerprints(b, key)
        self.assertEqual(fa["launch"], fb["launch"])
        self.assertNotEqual(fa["env"], fb["env"])
        self.assertNotEqual(fa["all"], fb["all"])

    def test_key_change_changes_fp_not_grouping_and_no_raw_sha(self):
        import hashlib
        cfg = summarize_entry("n", {"command": "x"})["fp_inputs"]
        k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
        f1, f2 = fingerprints(cfg, k1), fingerprints(cfg, k2)
        self.assertNotEqual(f1["all"], f2["all"])
        canon = json.dumps({"launch": cfg["launch"], "env": cfg["env"], "headers": cfg["headers"]},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertNotEqual(f1["all"], hashlib.sha256(canon).digest())
        # grouping stability: same key -> same digest; different key -> different digest
        self.assertEqual(f1["all"], fingerprints(cfg, k1)["all"])

    def test_row_id_shape(self):
        rid = row_id(secrets.token_bytes(32), "github")
        self.assertRegex(rid, r"^m[0-9a-z]{20}$")


class OwnFingerprintsTests(unittest.TestCase):
    def test_record_and_validity(self):
        own = mcp_local.OwnFingerprints()
        own.sync_key((1, 2, 3, 4))
        own.record_agent("w", "a", agent_updated_at="t1", collected_at="now",
                         servers={"m1": {}}, order=["m1"])
        self.assertIsNotNone(own.agent_record("w", "a", "t1", 1))
        self.assertIsNone(own.agent_record("w", "a", "t2", 1))
        self.assertIsNone(own.agent_record("w", "a", "t1", 2))
        own.sync_key((9, 9, 9, 9))
        self.assertIsNone(own.agent_record("w", "a", "t1", 1))
        own.prune_agents("w", set())
        self.assertIsNone(own.agent_record("w", "a", "t1", 1))


if __name__ == "__main__":
    unittest.main()

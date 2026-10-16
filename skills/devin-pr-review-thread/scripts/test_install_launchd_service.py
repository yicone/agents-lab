import pathlib

from install_launchd_service import LABEL, QUEUE, render_plist


def test_launchagent_is_login_persistent_and_proxy_bound():
    plist = render_plist("/repo", "/python", "/devin")
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == [
        "/python", "/repo/skills/devin-pr-review-thread/scripts/host_worker.py", "--queue", QUEUE
    ]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["EnvironmentVariables"]["https_proxy"] == "http://127.0.0.1:7890"
    assert plist["EnvironmentVariables"]["http_proxy"] == "http://127.0.0.1:7890"
    assert plist["EnvironmentVariables"]["all_proxy"] == "socks5://127.0.0.1:7890"


if __name__ == "__main__":
    test_launchagent_is_login_persistent_and_proxy_bound()
    print("ok")

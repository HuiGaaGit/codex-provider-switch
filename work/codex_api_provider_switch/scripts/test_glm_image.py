from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def read_key(config_path: Path) -> str:
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r'experimental_bearer_token\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("config.toml does not contain a GLM bearer token")
    return match.group(1)


def request(name: str, url: str, payload: dict[str, object], key: str) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            status, body = response.status, response.read(1200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(1200).decode("utf-8", "replace")
    except Exception as exc:
        status, body = 0, f"{type(exc).__name__}: {exc}"
    print(f"{name}\t{status}\t{body!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", type=Path)
    args = parser.parse_args()
    key = read_key(args.config_path)
    image_url = "data:image/png;base64," + base64.b64encode(PIXEL_PNG).decode("ascii")
    request(
        "responses",
        "https://open.bigmodel.cn/api/v1/responses",
        {
            "model": "glm-5.3-flash",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Reply with the single word: OK"},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
            "max_output_tokens": 32,
            "stream": False,
        },
        key,
    )
    request(
        "chat",
        "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        {
            "model": "glm-5.3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Reply with the single word: OK"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "max_tokens": 32,
            "stream": False,
        },
        key,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

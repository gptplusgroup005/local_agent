from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8787"


def request_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Talos API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Talos is not reachable at {base_url}: {exc}") from exc


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI bridge for Codex to call the local Talos tool server.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("state")
    subcommands.add_parser("context")
    subcommands.add_parser("projects")

    workspace = subcommands.add_parser("workspace")
    workspace.add_argument("path")
    workspace.add_argument("--fqbn", default="")

    read_file = subcommands.add_parser("read")
    read_file.add_argument("path")

    write_file = subcommands.add_parser("write")
    write_file.add_argument("path")
    write_source = write_file.add_mutually_exclusive_group(required=True)
    write_source.add_argument("--content")
    write_source.add_argument("--from-file")

    delete_file = subcommands.add_parser("delete")
    delete_file.add_argument("path")

    verify = subcommands.add_parser("verify")
    verify.add_argument("--path", default="")
    verify.add_argument("--fqbn", default="")

    args = parser.parse_args()

    if args.command == "state":
        print_json(request_json(args.base_url, "GET", "/api/state"))
        return
    if args.command == "context":
        print_json(request_json(args.base_url, "GET", "/api/arduino_context"))
        return
    if args.command == "projects":
        print_json(request_json(args.base_url, "GET", "/api/arduino_projects"))
        return
    if args.command == "workspace":
        print_json(request_json(args.base_url, "POST", "/api/arduino_workspace", {"path": args.path, "fqbn": args.fqbn}))
        return
    if args.command == "read":
        query = urllib.parse.urlencode({"path": args.path})
        print_json(request_json(args.base_url, "GET", f"/api/arduino_file?{query}"))
        return
    if args.command == "write":
        content = args.content
        if args.from_file:
            content = Path(args.from_file).read_text(encoding="utf-8")
        print_json(request_json(args.base_url, "POST", "/api/arduino_file", {"path": args.path, "content": content or ""}))
        return
    if args.command == "delete":
        print_json(request_json(args.base_url, "POST", "/api/arduino_delete", {"path": args.path}))
        return
    if args.command == "verify":
        payload = {key: value for key, value in {"path": args.path, "fqbn": args.fqbn}.items() if value}
        print_json(request_json(args.base_url, "POST", "/api/arduino_verify", payload))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main()

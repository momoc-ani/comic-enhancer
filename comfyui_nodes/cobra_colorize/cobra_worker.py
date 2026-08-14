from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import traceback
import types

from PIL import Image, ImageOps


def load_cobra_app():
    source = Path("/opt/cobra/app.py")
    if not source.is_file():
        raise RuntimeError(f"Cobra source not found: {source}")
    if str(source.parent) not in sys.path:
        sys.path.insert(0, str(source.parent))
    source_text = source.read_text(encoding="utf-8")
    source_text = source_text.split("\nwith gr.Blocks() as demo:", 1)[0]
    module = types.ModuleType("cobra_upstream_app")
    module.__file__ = str(source)
    exec(compile(source_text, str(source), "exec"), module.__dict__)
    return module


def colorize(cobra, request: dict) -> None:
    image_path = Path(request["image"])
    output_path = Path(request["output"])
    reference_paths = [Path(value) for value in request["references"]]
    if not image_path.is_file() or not all(path.is_file() for path in reference_paths):
        raise RuntimeError("Cobra input or reference image is missing")
    with Image.open(image_path) as source:
        page = ImageOps.exif_transpose(source).convert("RGB").copy()
    files = [SimpleNamespace(name=str(path)) for path in reference_paths]
    (
        extracted,
        hint_color,
        hint_mask,
        query_origin,
        extracted_origin,
        resolution,
    ) = cobra.extract_sketch_line_image(page, request["style"])
    gallery = cobra.colorize_image(
        extracted,
        files,
        resolution,
        int(request["seed"]),
        int(request["steps"]),
        int(request["top_k"]),
        hint_mask,
        hint_color,
        query_origin,
        extracted_origin,
    )
    if not gallery:
        raise RuntimeError("Cobra returned no image")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ImageOps.exif_transpose(gallery[0]).convert("RGB").save(output_path, "PNG")


def send_response(connection: socket.socket, payload: dict) -> None:
    connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")


def serve(socket_path: Path) -> None:
    cobra = load_cobra_app()
    socket_path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        socket_path.chmod(0o600)
        server.listen(1)
        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    request_file = connection.makefile("rb")
                    line = request_file.readline()
                    if not line:
                        continue
                    request = json.loads(line.decode("utf-8"))
                    action = request.get("action")
                    if action == "ping":
                        send_response(connection, {"ok": True})
                    elif action == "colorize":
                        colorize(cobra, request)
                        send_response(connection, {"ok": True})
                    else:
                        raise RuntimeError(f"Unsupported Cobra worker action: {action}")
                except Exception:
                    send_response(connection, {"ok": False, "error": traceback.format_exc()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    serve(args.socket)


if __name__ == "__main__":
    main()

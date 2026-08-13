#!/usr/bin/env python3
"""并行下载支持 Range 的 Hugging Face 镜像文件，并原子合并结果。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin


def download(
    url: str,
    target: Path,
    size: int,
    *,
    workers: int,
    chunk_size: int,
    retries: int,
) -> None:
    if target.is_file() and target.stat().st_size == size:
        print(f"已存在: {target}")
        return

    legacy = target.with_suffix(target.suffix + ".legacy.part")
    partial = target.with_suffix(target.suffix + ".part")
    if partial.is_file() and not legacy.exists():
        partial.rename(legacy)

    parts = target.with_suffix(target.suffix + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    ranges = [
        (start, min(size, start + chunk_size) - 1)
        for start in range(0, size, chunk_size)
    ]

    def fetch(item: tuple[int, int, int]) -> None:
        index, start, end = item
        path = parts / f"{index:06d}.part"
        expected = end - start + 1
        if path.is_file() and path.stat().st_size == expected:
            return
        request = urllib.request.Request(
            url,
            headers={"Range": f"bytes={start}-{end}", "User-Agent": "ComicEnhancer/0.1"},
        )
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                current_url = url
                for _ in range(6):
                    request = urllib.request.Request(
                        current_url,
                        headers={
                            "Range": f"bytes={start}-{end}",
                            "User-Agent": "ComicEnhancer/0.1",
                        },
                    )
                    try:
                        response = urllib.request.urlopen(request, timeout=120)
                    except urllib.error.HTTPError as error:
                        if error.code not in {301, 302, 303, 307, 308}:
                            raise
                        location = error.headers.get("Location")
                        if not location:
                            raise RuntimeError(
                                f"分片 {index} 跳转缺少 Location"
                            ) from error
                        current_url = urljoin(current_url, location)
                        continue
                    with response:
                        if response.status != 206:
                            raise RuntimeError(
                                f"服务器未返回 206: {response.status}"
                            )
                        data = response.read()
                    break
                else:
                    raise RuntimeError(f"分片 {index} 跳转次数过多")
                if len(data) != expected:
                    raise RuntimeError(
                        f"分片 {index} 长度错误: {len(data)} != {expected}"
                    )
                break
            except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                last_error = error
                if attempt >= retries:
                    raise RuntimeError(
                        f"分片 {index} 下载失败，已重试 {retries} 次: {error}"
                    ) from error
                time.sleep(min(2**attempt, 10))
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(fetch, (index, start, end))
            for index, (start, end) in enumerate(ranges)
        ]
        for index, future in enumerate(as_completed(futures), 1):
            future.result()
            print(f"分片完成 {index}/{len(futures)}", flush=True)

    temporary = target.with_suffix(target.suffix + ".merge.tmp")
    with temporary.open("wb") as output:
        for index in range(len(ranges)):
            part = parts / f"{index:06d}.part"
            with part.open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    if temporary.stat().st_size != size:
        raise RuntimeError(f"合并后长度错误: {temporary.stat().st_size} != {size}")
    temporary.replace(target)
    print(f"完成: {target} ({size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("target", type=Path)
    parser.add_argument("size", type=int)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--chunk-size", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()
    download(
        args.url,
        args.target,
        args.size,
        workers=args.workers,
        chunk_size=args.chunk_size,
        retries=args.retries,
    )


if __name__ == "__main__":
    main()

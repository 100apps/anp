"""官方大文件的断点分段下载；严格核对 Content-Range，不改变下载来源。"""
import argparse
import concurrent.futures
import re
import subprocess
from pathlib import Path
import time


def download(url, destination, jobs=12):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["curl", "-fILsS", "--connect-timeout", "10", "--max-time", "30", url], capture_output=True, check=True)
    sizes = re.findall(rb"(?im)^content-length:\s*(\d+)", head.stdout)
    if not sizes:
        raise RuntimeError("官方服务器未返回长度")
    size = int(sizes[-1])
    if destination.exists() and destination.stat().st_size == size:
        return destination
    cache = destination.parent / "ranges" / destination.name
    cache.mkdir(parents=True, exist_ok=True)
    block = 512 * 1024

    def part(index):
        start, end = index * block, min(size, (index + 1) * block) - 1
        target = cache / str(index)
        for attempt in range(12):
            offset = target.stat().st_size if target.exists() else 0
            if offset == end - start + 1:
                return
            if offset > end - start + 1:
                raise RuntimeError("分段缓存大小错误")
            segment, headers = cache / (str(index) + ".tmp"), cache / (str(index) + ".headers")
            response = subprocess.run(["curl", "-fLsS", "--connect-timeout", "8", "--max-time", "45",
                "--range", f"{start + offset}-{end}", "-D", str(headers), url, "-o", str(segment)], capture_output=True)
            ranges = re.findall(rb"(?im)^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)", headers.read_bytes() if headers.exists() else b"")
            if ranges and int(ranges[-1][0]) == start + offset and int(ranges[-1][2]) == size and segment.exists():
                data = segment.read_bytes()
                if len(data) <= end - start + 1 - offset:
                    with target.open("ab") as stream:
                        stream.write(data)
            segment.unlink(missing_ok=True)
            if target.exists() and target.stat().st_size == end - start + 1:
                return
            time.sleep(min(attempt + 1, 5))
        raise RuntimeError(f"官方分段下载失败：{destination.name} #{index}")

    count = (size + block - 1) // block
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for done, _ in enumerate(executor.map(part, range(count)), 1):
            if done % 10 == 0 or done == count:
                print(f"{destination.name}: {done}/{count} 分段", flush=True)
    temporary = destination.with_name(destination.name + ".complete")
    with temporary.open("wb") as stream:
        for index in range(count):
            stream.write((cache / str(index)).read_bytes())
    assert temporary.stat().st_size == size
    temporary.replace(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--jobs", type=int, default=12)
    options = parser.parse_args()
    download(options.url, options.destination, options.jobs)

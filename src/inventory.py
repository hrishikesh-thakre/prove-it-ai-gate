import json
from pathlib import Path


def inventory_path(path: str, skip_patterns: list[str] | None = None) -> dict:
    if skip_patterns is None:
        skip_patterns = [".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"]

    root = Path(path)
    if not root.is_dir():
        return {"error": f"Not a directory: {path}", "files": []}

    files: list[dict] = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        if any(pat in entry.parts for pat in skip_patterns):
            continue
        rel = str(entry.relative_to(root))
        stat = entry.stat()
        files.append({"path": rel, "size_bytes": stat.st_size})

    return {
        "root": str(root),
        "file_count": len(files),
        "total_size_bytes": sum(f["size_bytes"] for f in files),
        "files": sorted(files, key=lambda f: f["path"]),
    }


def write_inventory(path: str, output_path: str) -> str:
    data = inventory_path(path)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return output_path

from pathlib import Path
import sys

# === НАСТРОЙКИ ===
OUTPUT_FILENAME = "project_context.txt"
MAX_FILE_SIZE_KB = 200          # Файлы тяжелее этого лимита пропустятся с пометкой
ADD_LINE_NUMBERS = True         # Нумерация строк — сильно упрощает поиск багов

IGNORE_DIRS = {
    ".git", ".svn", ".hg",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".next", ".nuxt", "dist", "build", "out", "coverage",
    ".idea", ".vscode", ".vs", "target", "bin", "obj", ".obsidian",
}

IGNORE_FILES = {
    OUTPUT_FILENAME,
    "pack_project.py",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock",
    ".DS_Store", "Thumbs.db",
}

IGNORE_EXTS = {
    # Изображения, медиа, шрифты
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg", ".psd",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".mkv",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # Архивы, бинарники, базы, модели
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo", ".pyd", ".class", ".o",
    ".db", ".sqlite", ".sqlite3", ".pdf", ".docx", ".xlsx",
    ".gguf", ".pt", ".pth", ".onnx", ".safetensors", ".bin", ".map",
}

IGNORE_SUFFIXES = (".min.js", ".min.css", ".bundle.js")


def is_secret_file(name: str) -> bool:
    low = name.lower()
    if low.endswith((".example", ".sample", ".template")):
        return False
    if low == ".env" or low.startswith(".env."):
        return True
    return low.endswith((".pem", ".key", ".p12", ".pfx"))


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except OSError:
        return True


def read_text_safely(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="replace")


def build_tree(root_name: str, rel_paths: list[Path]) -> str:
    tree: dict = {}
    for rel in rel_paths:
        node = tree
        for part in rel.parts:
            node = node.setdefault(part, {})

    lines = [f"{root_name}/"]

    def render(node: dict, prefix: str = "") -> None:
        items = sorted(node.items(), key=lambda item: (len(item[1]) == 0, item[0].lower()))
        for idx, (name, child) in enumerate(items):
            is_last = idx == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{name}{'/' if child else ''}")
            if child:
                render(child, prefix + ("    " if is_last else "│   "))

    render(tree)
    return "\n".join(lines)


def pack_project(root_dir: Path) -> None:
    root_dir = root_dir.resolve()
    output_path = root_dir / OUTPUT_FILENAME

    included: list[Path] = []
    skipped_large: list[tuple[Path, float]] = []
    skipped_secrets: list[Path] = []

    for path in sorted(root_dir.rglob("*")):
        rel = path.relative_to(root_dir)

        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if not path.is_file():
            continue

        name = path.name
        if name in IGNORE_FILES or name.lower().endswith(IGNORE_SUFFIXES):
            continue
        if path.suffix.lower() in IGNORE_EXTS:
            continue
        if is_secret_file(name):
            skipped_secrets.append(rel)
            continue

        size_kb = path.stat().st_size / 1024
        if size_kb > MAX_FILE_SIZE_KB:
            skipped_large.append((rel, size_kb))
            continue

        if is_binary(path):
            continue

        included.append(rel)

    tree_str = build_tree(root_dir.name, included)
    parts: list[str] = [
        f"PROJECT: {root_dir.name}",
        f"FILES INCLUDED: {len(included)}",
        "",
        "=== DIRECTORY TREE ===",
        tree_str,
        "",
    ]

    if skipped_large:
        parts.append("=== SKIPPED (TOO LARGE) ===")
        for rel, size_kb in skipped_large:
            parts.append(f"- {rel.as_posix()} ({size_kb:.1f} KB)")
        parts.append("")

    for rel in included:
        full_path = root_dir / rel
        content = read_text_safely(full_path)

        if ADD_LINE_NUMBERS:
            lines = content.splitlines()
            width = max(3, len(str(len(lines))))
            content = "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, 1))

        parts.extend([
            "=" * 80,
            f"FILE: {rel.as_posix()}",
            "=" * 80,
            content,
            "",
        ])

    final_text = "\n".join(parts)
    output_path.write_text(final_text, encoding="utf-8")

    approx_tokens = len(final_text) // 4
    print(f"Готово: {output_path}")
    print(f"Собрано файлов: {len(included)}")
    print(f"Размер: {len(final_text.encode('utf-8')) / 1024:.1f} КБ (~{approx_tokens:,} токенов)")
    if skipped_secrets:
        print(f"Пропущено секретов (.env/ключи): {len(skipped_secrets)}")
    if skipped_large:
        print(f"Пропущено слишком больших файлов (больше {MAX_FILE_SIZE_KB} КБ): {len(skipped_large)}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    pack_project(target)
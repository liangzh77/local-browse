#!/usr/bin/env python3
"""
LocalBrowse 本地文件浏览服务器
用法：python server.py 或直接双击 start.bat
"""

import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from threading import Lock

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:  # pragma: no cover - tkinter may be unavailable on some systems
    tk = None
    filedialog = None

BASE_DIR = Path(__file__).parent.resolve()
START_ROOT = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else BASE_DIR
CURRENT_ROOT = START_ROOT if START_ROOT.is_dir() else BASE_DIR
ROOT_LOCK = Lock()

EXCLUDE = {'.git', '.claude', '__pycache__', 'node_modules', '.vscode', '.idea'}
VISIBLE_DOT_ENTRIES = {'.memory'}

TEXT_EXTS  = {'.md', '.txt', '.py', '.js', '.ts', '.jsx', '.tsx',
              '.cpp', '.c', '.h', '.ino', '.json', '.yaml', '.yml',
              '.css', '.html', '.sh', '.bat', '.rs', '.go', '.java',
              '.rb', '.php', '.swift', '.kt', '.sql', '.toml', '.ini',
              '.env', '.gitignore', '.cmake', '.makefile',
              '.scad', '.xml', '.csv', '.tsv', '.r', '.m', '.lua',
              '.dart', '.ex', '.exs', '.clj', '.hs', '.vim', '.conf',
              '.cfg', '.properties', '.gradle', '.tf', '.proto'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
SVG_EXTS   = {'.svg'}
ALL_EXTS   = TEXT_EXTS | IMAGE_EXTS | SVG_EXTS


def get_current_root() -> Path:
    with ROOT_LOCK:
        return CURRENT_ROOT


def set_current_root(path: Path) -> None:
    global CURRENT_ROOT
    with ROOT_LOCK:
        CURRENT_ROOT = path


def build_tree(path: Path, rel: Path = Path('.')):
    result = []
    try:
        entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return result

    for entry in entries:
        if (entry.name.startswith('.') and entry.name not in VISIBLE_DOT_ENTRIES) or entry.name in EXCLUDE:
            continue
        rel_entry = rel / entry.name
        if entry.is_dir():
            children = build_tree(entry, rel_entry)
            if children:
                result.append({
                    'name': entry.name,
                    'path': str(rel_entry).replace('\\', '/'),
                    'type': 'dir',
                    'children': children,
                })
        elif entry.is_file() and entry.suffix.lower() in ALL_EXTS:
            result.append({
                'name': entry.name,
                'path': str(rel_entry).replace('\\', '/'),
                'type': 'file',
                'ext':  entry.suffix.lower().lstrip('.'),
            })
    return result


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/tree':
            root = get_current_root()
            self._send_json({
                'root': str(root),
                'items': build_tree(root),
            })

        elif parsed.path == '/api/file':
            params = urllib.parse.parse_qs(parsed.query)
            rel = params.get('path', [''])[0]
            self._serve_md(rel)

        elif parsed.path == '/api/current-root':
            self._send_json({'root': str(get_current_root())})

        elif parsed.path == '/api/raw':
            params = urllib.parse.parse_qs(parsed.query)
            rel = params.get('path', [''])[0]
            self._serve_raw(rel)

        elif parsed.path.startswith('/root/'):
            rel = urllib.parse.unquote(parsed.path[len('/root/'):])
            self._serve_raw(rel)

        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/select-root':
            self._select_root()
            return

        self.send_error(404)

    def _serve_md(self, rel_path):
        full = self._resolve_in_root(rel_path)
        if full is None:
            self.send_error(403)
            return

        if not full.is_file() or full.suffix.lower() not in TEXT_EXTS | SVG_EXTS:
            self.send_error(404)
            return

        content = full.read_text(encoding='utf-8', errors='replace')
        self._send_json({'content': content, 'path': rel_path,
                         'ext': full.suffix.lower().lstrip('.')})

    def _serve_raw(self, rel_path):
        full = self._resolve_in_root(rel_path)
        if full is None:
            self.send_error(403)
            return
        if not full.is_file() or full.suffix.lower() not in ALL_EXTS:
            self.send_error(404)
            return

        try:
            body = full.read_bytes()
        except OSError:
            self.send_error(404)
            return

        content_type = self.guess_type(str(full))
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _select_root(self):
        if tk is None or filedialog is None:
            self._send_json({
                'ok': False,
                'error': 'tkinter unavailable',
                'root': str(get_current_root()),
            })
            return

        selected = self._ask_directory()
        if not selected:
            self._send_json({'ok': False, 'cancelled': True, 'root': str(get_current_root())})
            return

        root = Path(selected).resolve()
        if not root.is_dir():
            self._send_json({'ok': False, 'error': 'invalid directory', 'root': str(get_current_root())})
            return

        set_current_root(root)
        self._send_json({'ok': True, 'root': str(root), 'items': build_tree(root)})

    def _ask_directory(self):
        holder = {}

        def choose():
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            try:
                holder['path'] = filedialog.askdirectory(
                    title='选择要浏览的文件夹',
                    initialdir=str(get_current_root()),
                )
            finally:
                root.destroy()

        choose()
        return holder.get('path', '')

    def _resolve_in_root(self, rel_path):
        try:
            root = get_current_root()
            full = (root / rel_path).resolve()
            full.relative_to(root)
            return full
        except (ValueError, Exception):
            return None

    def _send_html(self, path: Path):
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 不打印每条请求日志，保持终端干净


if __name__ == '__main__':
    # Let the OS choose an actually available port instead of probing first.
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as srv:
        port = srv.server_address[1]
        url = f'http://127.0.0.1:{port}'
        print(f"LocalBrowse -> {url}")
        print(f"Root: {get_current_root()}")
        print("Ctrl+C 停止")

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")

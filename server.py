#!/usr/bin/env python3
"""
LocalBrowse 本地文件浏览服务器
用法：python server.py 或直接双击 start.bat
"""

import http.server
import json
import mimetypes
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

SAMPLE_BYTES = 8192
MAX_TEXT_BYTES = 2 * 1024 * 1024

CODE_EXTS = {
    '.bat', '.c', '.cfg', '.clj', '.cmake', '.conf', '.cpp', '.cs', '.css',
    '.dart', '.env', '.ex', '.exs', '.go', '.gradle', '.h', '.hs', '.ini',
    '.ino', '.java', '.js', '.json', '.jsx', '.kt', '.lua', '.m',
    '.makefile', '.mod', '.php', '.properties', '.proto', '.py', '.r', '.rb',
    '.rs', '.scad', '.sh', '.sql', '.sum', '.swift', '.tf', '.toml', '.ts',
    '.tsx', '.vim', '.xml', '.yaml', '.yml',
}

TEXT_NAMES = {'.gitignore', 'makefile', 'dockerfile'}

IMAGE_MAGIC = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
    (b'RIFF', 'image/webp'),
)
AUDIO_MAGIC = (
    (b'ID3', 'audio/mpeg'),
    (b'\xff\xfb', 'audio/mpeg'),
    (b'OggS', 'audio/ogg'),
    (b'fLaC', 'audio/flac'),
)
VIDEO_EXTS = {'.mp4', '.webm', '.ogv', '.mov', '.m4v'}
AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.avif'}


def file_ext(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix[1:] if suffix else path.name.lower().lstrip('.')


def looks_binary(sample: bytes) -> bool:
    if b'\x00' in sample:
        return True
    if not sample:
        return False
    control = sum(1 for b in sample if b < 32 and b not in (8, 9, 10, 12, 13, 27))
    return control / len(sample) > 0.30


def decode_text_bytes(data: bytes):
    encodings = ('utf-8-sig', 'utf-16', 'gb18030')
    for encoding in encodings:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return data.decode('utf-8', errors='replace'), 'utf-8-replace'


def sniff_media(path: Path, sample: bytes):
    ext = path.suffix.lower()
    lower_name = path.name.lower()

    if lower_name.endswith('.svg') or sample.lstrip().lower().startswith(b'<svg'):
        return 'svg', 'image/svg+xml'
    if sample.startswith(b'%PDF-'):
        return 'pdf', 'application/pdf'
    for magic, mime in IMAGE_MAGIC:
        if sample.startswith(magic):
            if mime == 'image/webp' and sample[8:12] != b'WEBP':
                continue
            return 'image', mime
    for magic, mime in AUDIO_MAGIC:
        if sample.startswith(magic):
            return 'audio', mime
    if ext in IMAGE_EXTS:
        return 'image', mimetypes.guess_type(path.name)[0] or 'image/*'
    if ext in AUDIO_EXTS:
        return 'audio', mimetypes.guess_type(path.name)[0] or 'audio/*'
    if ext in VIDEO_EXTS:
        return 'video', mimetypes.guess_type(path.name)[0] or 'video/*'
    return None, None


def classify_file(path: Path):
    try:
        with path.open('rb') as f:
            sample = f.read(SAMPLE_BYTES)
    except OSError:
        return {'previewable': False, 'previewKind': 'unreadable', 'mime': None}

    media_kind, mime = sniff_media(path, sample)
    if media_kind:
        return {'previewable': True, 'previewKind': media_kind, 'mime': mime}

    if looks_binary(sample):
        return {'previewable': False, 'previewKind': 'binary', 'mime': mimetypes.guess_type(path.name)[0]}

    text, _encoding = decode_text_bytes(sample)
    stripped = text.lstrip().lower()
    ext = path.suffix.lower()
    lower_name = path.name.lower()

    if ext == '.md':
        kind = 'markdown'
        mime = 'text/markdown'
    elif ext in ('.html', '.htm') or stripped.startswith('<!doctype html') or stripped.startswith('<html'):
        kind = 'html'
        mime = 'text/html'
    elif ext in CODE_EXTS or lower_name in TEXT_NAMES:
        kind = 'code'
        mime = 'text/plain'
    else:
        kind = 'text'
        mime = 'text/plain'

    return {'previewable': True, 'previewKind': kind, 'mime': mime}


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
        if entry.is_dir() and (
            entry.name in EXCLUDE
            or (entry.name.startswith('.') and entry.name not in VISIBLE_DOT_ENTRIES)
        ):
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
        elif entry.is_file():
            preview = classify_file(entry)
            result.append({
                'name': entry.name,
                'path': str(rel_entry).replace('\\', '/'),
                'type': 'file',
                'ext': file_ext(entry),
                **preview,
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
            self._serve_text(rel)

        elif parsed.path == '/api/current-root':
            self._send_json({'root': str(get_current_root())})

        elif parsed.path == '/api/raw':
            params = urllib.parse.parse_qs(parsed.query)
            rel = params.get('path', [''])[0]
            self._serve_raw(rel)

        elif parsed.path.startswith('/root/'):
            rel = urllib.parse.unquote(parsed.path[len('/root/'):])
            self._serve_raw(rel)

        elif parsed.path == '/api/tracker/data':
            self._serve_tracker_data()

        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/select-root':
            self._select_root()
            return

        self.send_error(404)

    def _serve_text(self, rel_path):
        full = self._resolve_in_root(rel_path)
        if full is None:
            self.send_error(403)
            return

        if not full.is_file():
            self.send_error(404)
            return

        info = classify_file(full)
        if not info['previewable'] or info['previewKind'] not in {'markdown', 'html', 'svg', 'code', 'text'}:
            self.send_error(415)
            return

        try:
            data = full.read_bytes()
        except OSError:
            self.send_error(404)
            return

        truncated = len(data) > MAX_TEXT_BYTES
        if truncated:
            data = data[:MAX_TEXT_BYTES]

        content, encoding = decode_text_bytes(data)
        self._send_json({
            'content': content,
            'path': rel_path,
            'ext': file_ext(full),
            'kind': info['previewKind'],
            'encoding': encoding,
            'truncated': truncated,
        })

    def _serve_raw(self, rel_path):
        full = self._resolve_in_root(rel_path)
        if full is None:
            self.send_error(403)
            return
        if not full.is_file():
            self.send_error(404)
            return

        try:
            body = full.read_bytes()
        except OSError:
            self.send_error(404)
            return

        info = classify_file(full)
        content_type = info.get('mime') or mimetypes.guess_type(full.name)[0] or 'application/octet-stream'
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

    def _find_tracker_data_dir(self):
        root = get_current_root()
        tracker_dir_name = '\u6295\u8d44\u8ddf\u8e2a'
        candidates = [
            root / 'projects' / tracker_dir_name / 'data',
            root / tracker_dir_name / 'data',
            root / 'data',
        ]
        for data_dir in candidates:
            try:
                resolved = data_dir.resolve()
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue
            if resolved.is_dir():
                return resolved
        return None

    def _serve_tracker_data(self):
        data_dir = self._find_tracker_data_dir()
        if data_dir is None:
            self.send_error(404, 'Tracker data directory not found')
            return

        try:
            sources = json.loads((data_dir / 'sources.json').read_text(encoding='utf-8'))
            signals = json.loads((data_dir / 'signals.json').read_text(encoding='utf-8'))
            trades = json.loads((data_dir / 'trades.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            self.send_error(500, f'Failed to read tracker data: {exc}')
            return

        self._send_json({**sources, **signals, **trades})

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

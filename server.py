#!/usr/bin/env python3
"""
LocalBrowse 本地文件浏览服务器
用法：python server.py 或直接双击 start.bat
"""

import http.server
import hashlib
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
HAS_START_ARG = len(sys.argv) > 1
START_ROOT = Path(sys.argv[1]).expanduser().resolve() if HAS_START_ARG else BASE_DIR
CONFIG_DIR = Path(os.environ.get('LOCALBROWSE_CONFIG_DIR', Path.home() / '.localbrowse'))
ROOTS_FILE = CONFIG_DIR / 'roots.json'
DEFAULT_ROOT = START_ROOT if START_ROOT.is_dir() else BASE_DIR
OPEN_ROOTS = []
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
SVG_EXTS = {'.svg'}
PDF_EXTS = {'.pdf'}
HTML_EXTS = {'.html', '.htm'}
MARKDOWN_EXTS = {'.md'}


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


def classify_file_for_listing(path: Path):
    ext = path.suffix.lower()
    lower_name = path.name.lower()

    if ext in MARKDOWN_EXTS:
        return {'previewable': True, 'previewKind': 'markdown', 'mime': 'text/markdown'}
    if ext in HTML_EXTS:
        return {'previewable': True, 'previewKind': 'html', 'mime': 'text/html'}
    if ext in SVG_EXTS:
        return {'previewable': True, 'previewKind': 'svg', 'mime': 'image/svg+xml'}
    if ext in PDF_EXTS:
        return {'previewable': True, 'previewKind': 'pdf', 'mime': 'application/pdf'}
    if ext in IMAGE_EXTS:
        return {'previewable': True, 'previewKind': 'image', 'mime': mimetypes.guess_type(path.name)[0] or 'image/*'}
    if ext in AUDIO_EXTS:
        return {'previewable': True, 'previewKind': 'audio', 'mime': mimetypes.guess_type(path.name)[0] or 'audio/*'}
    if ext in VIDEO_EXTS:
        return {'previewable': True, 'previewKind': 'video', 'mime': mimetypes.guess_type(path.name)[0] or 'video/*'}
    if ext in CODE_EXTS or lower_name in TEXT_NAMES:
        return {'previewable': True, 'previewKind': 'code', 'mime': 'text/plain'}
    return {'previewable': True, 'previewKind': 'text', 'mime': 'text/plain'}


def root_id(path: Path) -> str:
    return hashlib.sha1(str(path).lower().encode('utf-8')).hexdigest()[:12]


def root_payload(path: Path):
    return {
        'id': root_id(path),
        'name': path.name or str(path),
        'path': str(path),
    }


def load_open_roots():
    roots = []
    try:
        raw = json.loads(ROOTS_FILE.read_text(encoding='utf-8'))
        paths = raw.get('roots', []) if isinstance(raw, dict) else []
    except (OSError, json.JSONDecodeError):
        paths = []

    for value in paths:
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_dir() and all(existing != path for existing in roots):
            roots.append(path)

    if (HAS_START_ARG or not roots) and DEFAULT_ROOT.is_dir() and all(existing != DEFAULT_ROOT for existing in roots):
        roots.insert(0, DEFAULT_ROOT)

    return roots or [BASE_DIR]


def save_open_roots() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with ROOT_LOCK:
        paths = [str(path) for path in OPEN_ROOTS]
    ROOTS_FILE.write_text(json.dumps({'roots': paths}, ensure_ascii=False, indent=2), encoding='utf-8')


OPEN_ROOTS = load_open_roots()


def get_open_roots():
    with ROOT_LOCK:
        return list(OPEN_ROOTS)


def get_current_root() -> Path:
    with ROOT_LOCK:
        return OPEN_ROOTS[0] if OPEN_ROOTS else BASE_DIR


def add_open_root(path: Path) -> Path:
    with ROOT_LOCK:
        if all(existing != path for existing in OPEN_ROOTS):
            OPEN_ROOTS.append(path)
    save_open_roots()
    return path


def remove_open_root(root_id_value: str) -> bool:
    removed = False
    with ROOT_LOCK:
        kept = []
        for path in OPEN_ROOTS:
            if root_id(path) == root_id_value:
                removed = True
            else:
                kept.append(path)
        if removed:
            OPEN_ROOTS[:] = kept
    if removed:
        save_open_roots()
    return removed


def find_open_root(root_id_value=None):
    roots = get_open_roots()
    if not roots:
        return None
    if not root_id_value:
        return roots[0]
    for root in roots:
        if root_id(root) == root_id_value:
            return root
    return None


def build_dir(path: Path, rel: Path = Path('.')):
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
            result.append({
                'name': entry.name,
                'path': str(rel_entry).replace('\\', '/'),
                'type': 'dir',
                'loaded': False,
            })
        elif entry.is_file():
            preview = classify_file_for_listing(entry)
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
            self._send_json({
                'roots': self._build_roots_payload(),
            })

        elif parsed.path == '/api/dir':
            params = urllib.parse.parse_qs(parsed.query)
            root_id_value = params.get('root', [''])[0]
            rel = params.get('path', [''])[0]
            self._serve_dir(root_id_value, rel)

        elif parsed.path == '/api/file':
            params = urllib.parse.parse_qs(parsed.query)
            root_id_value = params.get('root', [''])[0]
            rel = params.get('path', [''])[0]
            self._serve_text(root_id_value, rel)

        elif parsed.path == '/api/current-root':
            roots = get_open_roots()
            self._send_json({
                'root': str(roots[0]) if roots else '',
                'roots': [root_payload(root) for root in roots],
            })

        elif parsed.path == '/api/raw':
            params = urllib.parse.parse_qs(parsed.query)
            root_id_value = params.get('root', [''])[0]
            rel = params.get('path', [''])[0]
            self._serve_raw(root_id_value, rel)

        elif parsed.path.startswith('/root/'):
            rest = urllib.parse.unquote(parsed.path[len('/root/'):])
            root_id_value, sep, rel = rest.partition('/')
            if sep:
                self._serve_raw(root_id_value, rel)
            else:
                self._serve_raw('', root_id_value)

        elif parsed.path == '/api/tracker/data':
            self._serve_tracker_data()

        elif parsed.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()

        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/select-root':
            self._select_root()
            return

        if parsed.path == '/api/remove-root':
            self._remove_root()
            return

        self.send_error(404)

    def _build_roots_payload(self):
        roots = []
        for root in get_open_roots():
            data = root_payload(root)
            try:
                data['items'] = build_dir(root)
            except OSError as exc:
                data['items'] = []
                data['error'] = str(exc)
            roots.append(data)
        return roots

    def _serve_dir(self, root_id_value, rel_path):
        full = self._resolve_in_root(root_id_value, rel_path)
        if full is None:
            self.send_error(403)
            return
        if not full.is_dir():
            self.send_error(404)
            return

        try:
            items = build_dir(full, Path(rel_path or '.'))
        except OSError as exc:
            self._send_json({'ok': False, 'error': str(exc), 'items': []})
            return

        self._send_json({
            'ok': True,
            'root': root_id_value or root_id(get_current_root()),
            'path': rel_path,
            'items': items,
        })

    def _serve_text(self, root_id_value, rel_path):
        full = self._resolve_in_root(root_id_value, rel_path)
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
            'root': root_id_value or root_id(get_current_root()),
            'ext': file_ext(full),
            'kind': info['previewKind'],
            'encoding': encoding,
            'truncated': truncated,
        })

    def _serve_raw(self, root_id_value, rel_path):
        full = self._resolve_in_root(root_id_value, rel_path)
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
                'roots': self._build_roots_payload(),
            })
            return

        selected = self._ask_directory()
        if not selected:
            self._send_json({'ok': False, 'cancelled': True, 'roots': self._build_roots_payload()})
            return

        root = Path(selected).resolve()
        if not root.is_dir():
            self._send_json({'ok': False, 'error': 'invalid directory', 'roots': self._build_roots_payload()})
            return

        add_open_root(root)
        self._send_json({'ok': True, 'root': root_payload(root), 'roots': self._build_roots_payload()})

    def _remove_root(self):
        try:
            length = int(self.headers.get('Content-Length', '0') or '0')
            body = self.rfile.read(length) if length else b'{}'
            data = json.loads(body.decode('utf-8') or '{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, 'Invalid JSON')
            return

        root_id_value = str(data.get('root') or '')
        if not root_id_value:
            self.send_error(400, 'Missing root id')
            return

        if not remove_open_root(root_id_value):
            self.send_error(400, 'Cannot remove root')
            return

        self._send_json({'ok': True, 'roots': self._build_roots_payload()})

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

    def _resolve_in_root(self, root_id_value, rel_path):
        try:
            root = find_open_root(root_id_value)
            if root is None:
                return None
            full = (root / rel_path).resolve()
            full.relative_to(root)
            return full
        except (ValueError, Exception):
            return None

    def _find_tracker_data_dir(self):
        tracker_dir_name = '\u6295\u8d44\u8ddf\u8e2a'
        for root in get_open_roots():
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

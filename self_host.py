#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from http.client import RemoteDisconnected
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "rustdesk"
DEFAULT_URL = "https://rustdesk.pinky.lilf.ir"
PLATFORMS = ("all", "windows", "macos", "linux", "android")
PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "npm_config_proxy",
    "npm_config_https_proxy",
    "npm_config_noproxy",
)


@dataclass(frozen=True)
class Paths:
    root: Path
    caddyfile: Path

    @property
    def self_host(self) -> Path:
        return self.root / ".self_host"

    @property
    def server(self) -> Path:
        return self.self_host / "server"

    @property
    def server_data(self) -> Path:
        return self.self_host / "server-data"

    @property
    def downloads(self) -> Path:
        return self.self_host / "downloads"

    @property
    def www(self) -> Path:
        return self.self_host / "www"

    @property
    def packages(self) -> Path:
        return self.www / "packages"

    @property
    def manifest(self) -> Path:
        return self.packages / "manifest.json"

    @property
    def hbbs(self) -> Path:
        return self.server / "hbbs"

    @property
    def hbbr(self) -> Path:
        return self.server / "hbbr"


@dataclass(frozen=True)
class Sessions:
    hbbs: str = "rustdesk-hbbs"
    hbbr: str = "rustdesk-hbbr"


@dataclass(frozen=True)
class Ports:
    tcp: tuple[int, ...] = (21115, 21116, 21117, 21118, 21119)
    udp: tuple[int, ...] = (21116,)


@dataclass(frozen=True)
class AppConfig:
    paths: Paths
    sessions: Sessions = Sessions()
    ports: Ports = Ports()
    caddy_begin: str = "# BEGIN RustDesk self-host"
    caddy_end: str = "# END RustDesk self-host"
    default_url: str = DEFAULT_URL
    client_repo: str = "rustdesk/rustdesk"
    server_repo: str = "rustdesk/rustdesk-server"


@dataclass(frozen=True)
class UrlInfo:
    raw: str
    host: str
    https: str
    http: str


@dataclass(frozen=True)
class ClientAsset:
    name: str
    platform: str
    size: int
    sha256: str


def default_config() -> AppConfig:
    root = Path(__file__).resolve().parent
    caddyfile = Path(os.environ.get("CADDYFILE", str(Path.home() / "Caddyfile"))).expanduser()
    return AppConfig(paths=Paths(root=root, caddyfile=caddyfile))


def log(message: str) -> None:
    print(f"[{APP_NAME}] {message}")


def die(message: str) -> None:
    print(f"[{APP_NAME}] error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args, *, cwd=None, check=True, capture=False, quiet=False):
    kwargs = {"cwd": cwd, "check": check, "text": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.run(args, **kwargs)


def require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        die(f"missing required command: {name}")


def normalize_url(raw: str) -> UrlInfo:
    value = (raw or DEFAULT_URL).strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        die("URL must look like https://host or http://host")
    return UrlInfo(
        raw=f"{parsed.scheme}://{parsed.netloc}",
        host=parsed.netloc,
        https=f"https://{parsed.netloc}",
        http=f"http://{parsed.netloc}",
    )


def server_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64v8"
    if machine in ("armv7l", "armv7"):
        return "armv7"
    if machine in ("i386", "i686", "x86"):
        return "i386"
    die(f"unsupported server architecture: {platform.machine()}")


def fetch_json(url: str):
    if shutil.which("curl"):
        result = run(
            [
                "curl",
                "-fsSL",
                "--retry",
                "10",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "--connect-timeout",
                "20",
                "--max-time",
                "120",
                url,
            ],
            capture=True,
        )
        return json.loads(result.stdout)
    request = urllib.request.Request(url, headers={"User-Agent": "rustdesk-self-host/1"})
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except RemoteDisconnected:
            if attempt == 9:
                raise
            time.sleep(2)
    die(f"failed to fetch {url}")


def download(url: str, *, destination: Path, expected_sha256: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    if shutil.which("aria2c"):
        run(
            [
                "aria2c",
                "--continue=true",
                "--max-connection-per-server=8",
                "--split=8",
                "--min-split-size=1M",
                "--retry-wait=2",
                "--max-tries=20",
                "--connect-timeout=20",
                "--timeout=120",
                "--allow-overwrite=true",
                "--auto-file-renaming=false",
                "--dir",
                str(tmp.parent),
                "--out",
                tmp.name,
                url,
            ]
        )
        tmp.replace(destination)
    elif shutil.which("curl"):
        run(
            [
                "curl",
                "-fL",
                "-C",
                "-",
                "--retry",
                "20",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "--connect-timeout",
                "20",
                "--max-time",
                "1800",
                "-o",
                str(tmp),
                url,
            ]
        )
        tmp.replace(destination)
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "rustdesk-self-host/1"})
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
        tmp.replace(destination)
    verify_sha256(destination, expected_sha256=expected_sha256)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_sha256(asset) -> str | None:
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest.removeprefix("sha256:")
    return None


def verify_sha256(path: Path, *, expected_sha256: str | None) -> None:
    if not expected_sha256:
        return
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        path.unlink(missing_ok=True)
        die(f"checksum mismatch for {path.name}: expected {expected_sha256}, got {actual}")


def latest_release(repo: str):
    return fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")


def choose_asset(release, *, predicate):
    for asset in release.get("assets", []):
        if predicate(asset["name"]):
            return asset
    die(f"no matching asset found in release {release.get('tag_name', '<unknown>')}")


def ensure_server_binaries(config: AppConfig) -> None:
    if config.paths.hbbs.exists() and config.paths.hbbr.exists():
        return
    require_cmd("unzip")
    asset_name = f"rustdesk-server-linux-{server_arch()}.zip"
    release = latest_release(config.server_repo)
    asset = choose_asset(release, predicate=lambda name: name == asset_name)
    archive = config.paths.downloads / asset_name
    log(f"downloading {asset_name} from rustdesk-server {release['tag_name']}")
    download(asset["browser_download_url"], destination=archive, expected_sha256=asset_sha256(asset))
    extract_dir = config.paths.self_host / "server-extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract_dir)
    hbbs = next(extract_dir.rglob("hbbs"), None)
    hbbr = next(extract_dir.rglob("hbbr"), None)
    if not hbbs or not hbbr:
        die(f"{asset_name} did not contain hbbs and hbbr")
    config.paths.server.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hbbs, config.paths.hbbs)
    shutil.copy2(hbbr, config.paths.hbbr)
    config.paths.hbbs.chmod(0o755)
    config.paths.hbbr.chmod(0o755)
    shutil.rmtree(extract_dir)


def port_in_use_tcp(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_in_use_udp(port: int) -> bool:
    result = run(["ss", "-H", "-lun", f"( sport = :{port} )"], capture=True, check=False)
    return bool(result.stdout.strip())


def check_ports_free(config: AppConfig) -> None:
    require_cmd("ss")
    busy = []
    for port in config.ports.tcp:
        if port_in_use_tcp(port):
            busy.append(f"TCP {port}")
    for port in config.ports.udp:
        if port_in_use_udp(port):
            busy.append(f"UDP {port}")
    if busy:
        die("required RustDesk port(s) already in use: " + ", ".join(busy))


def tmux_env_args() -> list[str]:
    args = []
    for name in PROXY_ENV_NAMES:
        if name in os.environ:
            args.extend(["-e", f"{name}={os.environ[name]}"])
    return args


def tmux_kill(session: str) -> None:
    run(["tmux", "kill-session", "-t", session], check=False, quiet=True)


def tmuxnew(session: str, *, cwd: Path, command) -> None:
    tmux_kill(session)
    args = ["tmux", "new", "-d", "-s", session, "-c", str(cwd)]
    args.extend(tmux_env_args())
    args.append("--")
    args.append(" ".join(shlex.quote(str(part)) for part in command))
    run(args)


def stop(config: AppConfig) -> None:
    require_cmd("tmux")
    tmux_kill(config.sessions.hbbs)
    tmux_kill(config.sessions.hbbr)
    log("stopped RustDesk tmux sessions")


def public_key(config: AppConfig) -> str:
    key_path = config.paths.server_data / "id_ed25519.pub"
    if not key_path.exists():
        return ""
    return key_path.read_text(encoding="utf-8").strip()


def wait_for_public_key(config: AppConfig) -> str:
    for _ in range(30):
        key = public_key(config)
        if key:
            return key
        time.sleep(1)
    die(f"server did not create {config.paths.server_data / 'id_ed25519.pub'}")


def rustdesk_toml(url: UrlInfo, *, key: str) -> str:
    return (
        f"rendezvous_server = '{url.host}'\n"
        "nat_type = 1\n"
        "serial = 0\n"
        "\n"
        "[options]\n"
        f"custom-rendezvous-server = '{url.host}'\n"
        f"relay-server = '{url.host}'\n"
        f"key = '{key}'\n"
    )


def read_manifest(config: AppConfig) -> dict:
    if not config.paths.manifest.exists():
        return {"generated_at": None, "release": None, "assets": []}
    return json.loads(config.paths.manifest.read_text(encoding="utf-8"))


def toml_shell_block(url: UrlInfo, *, key: str) -> str:
    return rustdesk_toml(url, key=key).rstrip()


def write_install_sh(config: AppConfig, *, url: UrlInfo, key: str) -> None:
    toml = toml_shell_block(url, key=key)
    script = f"""#!/bin/sh
set -eu

BASE_URL=${{BASE_URL:-{url.https}}}
HOST={shlex.quote(url.host)}
KEY={shlex.quote(key)}

if [ -z "$KEY" ]; then
	echo "RustDesk server key has not been generated yet. Run ./self_host.py setup on the server first." >&2
	exit 1
fi

have() {{
	command -v "$1" >/dev/null 2>&1
}}

download() {{
	curl -fsSL "$1" -o "$2"
}}

manifest_asset() {{
	curl -fsSL "$BASE_URL/packages/packages.txt" | grep -E "$1" | head -n 1
}}

write_config() {{
	os=$(uname -s 2>/dev/null || echo unknown)
	case "$os" in
		Darwin) cfg="$HOME/Library/Application Support/RustDesk/config/RustDesk2.toml" ;;
		Linux) cfg="$HOME/.config/rustdesk/RustDesk2.toml" ;;
		*) echo "unsupported OS for config: $os" >&2; return 1 ;;
	esac
	mkdir -p "$(dirname "$cfg")"
	cat > "$cfg" <<'CONFIG'
{toml}
CONFIG
	echo "wrote $cfg"
}}

install_linux() {{
	arch=$(uname -m 2>/dev/null || echo unknown)
	tmp=$(mktemp -d)
	trap 'rm -rf "$tmp"' EXIT INT TERM
	case "$arch" in
		x86_64|amd64)
			deb=$(manifest_asset 'x86_64\\.deb$' || true)
			appimage=$(manifest_asset 'x86_64\\.AppImage$' || true)
			;;
		aarch64|arm64)
			deb=$(manifest_asset 'aarch64\\.deb$' || true)
			appimage=$(manifest_asset 'aarch64\\.AppImage$' || true)
			;;
		*) echo "unsupported Linux architecture: $arch" >&2; return 1 ;;
	esac
	if [ -n "${{deb:-}}" ] && have dpkg; then
		download "$BASE_URL/packages/$deb" "$tmp/rustdesk.deb"
		if [ "$(id -u)" -eq 0 ]; then
			dpkg -i "$tmp/rustdesk.deb"
		elif have sudo; then
			sudo dpkg -i "$tmp/rustdesk.deb"
		else
			echo "need root or sudo to install deb" >&2
			return 1
		fi
	elif [ -n "${{appimage:-}}" ]; then
		mkdir -p "$HOME/.local/bin"
		download "$BASE_URL/packages/$appimage" "$HOME/.local/bin/rustdesk.AppImage"
		chmod +x "$HOME/.local/bin/rustdesk.AppImage"
		echo "installed AppImage to $HOME/.local/bin/rustdesk.AppImage"
	else
		echo "no mirrored Linux package matched this architecture" >&2
		return 1
	fi
}}

install_macos() {{
	arch=$(uname -m 2>/dev/null || echo unknown)
	tmp=$(mktemp -d)
	mount_point="$tmp/mnt"
	trap 'hdiutil detach "$mount_point" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT INT TERM
	case "$arch" in
		arm64|aarch64)
			dmg=$(manifest_asset 'aarch64\\.dmg$' || true)
			;;
		x86_64|amd64)
			dmg=$(manifest_asset 'x86_64\\.dmg$' || true)
			;;
		*) echo "unsupported macOS architecture: $arch" >&2; return 1 ;;
	esac
	if [ -z "${{dmg:-}}" ]; then
		echo "no mirrored macOS DMG matched this architecture; run ./self_host.py mirror macos on the server" >&2
		return 1
	fi
	download "$BASE_URL/packages/$dmg" "$tmp/rustdesk.dmg"
	mkdir -p "$mount_point"
	hdiutil attach "$tmp/rustdesk.dmg" -nobrowse -readonly -mountpoint "$mount_point" >/dev/null
	app=$(find "$mount_point" -maxdepth 2 -name '*.app' -type d | head -n 1)
	if [ -z "$app" ]; then
		echo "mirrored DMG did not contain a macOS app bundle" >&2
		return 1
	fi
	target="/Applications/$(basename "$app")"
	if [ -w /Applications ]; then
		rm -rf "$target"
		ditto "$app" "$target"
	else
		if ! have sudo; then
			echo "need write access to /Applications or sudo to install RustDesk" >&2
			return 1
		fi
		sudo rm -rf "$target"
		sudo ditto "$app" "$target"
	fi
	echo "installed $target"
}}

case "$(uname -s 2>/dev/null || echo unknown)" in
	Linux) install_linux || true ;;
	Darwin) install_macos ;;
	*) echo "unsupported OS for installer" >&2 ;;
esac

write_config
if have rustdesk; then
	rustdesk --option custom-rendezvous-server "$HOST" >/dev/null 2>&1 || true
	rustdesk --option relay-server "$HOST" >/dev/null 2>&1 || true
	rustdesk --option key "$KEY" >/dev/null 2>&1 || true
fi
echo "RustDesk is configured for $HOST"
"""
    path = config.paths.www / "install.sh"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def write_install_ps1(config: AppConfig, *, url: UrlInfo, key: str) -> None:
    toml = rustdesk_toml(url, key=key).replace("'", "''").rstrip()
    script = f"""$ErrorActionPreference = "Stop"
$BaseUrl = "{url.https}"
$HostName = "{url.host}"
$Key = "{key}"
if (-not $Key) {{ throw "RustDesk server key has not been generated yet. Run ./self_host.py setup on the server first." }}
$Manifest = Invoke-RestMethod "$BaseUrl/packages/manifest.json"
$Asset = $Manifest.assets | Where-Object {{ $_.name -like "*x86_64.msi" }} | Select-Object -First 1
if (-not $Asset) {{ $Asset = $Manifest.assets | Where-Object {{ $_.name -like "*x86_64.exe" }} | Select-Object -First 1 }}
if (-not $Asset) {{ throw "No mirrored Windows x86_64 installer found. Run self_host.py mirror windows on the server." }}
$Tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "rustdesk-self-host")
$Installer = Join-Path $Tmp.FullName $Asset.name
Invoke-WebRequest "$BaseUrl/packages/$($Asset.name)" -OutFile $Installer
if ($Installer.EndsWith(".msi")) {{
	Start-Process msiexec.exe -ArgumentList "/i `"$Installer`" /qn" -Wait
}} else {{
	Start-Process $Installer -ArgumentList "--silent-install" -Wait
}}
$ConfigDir = Join-Path $env:APPDATA "RustDesk\\config"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
@'
{toml}
'@ | Set-Content -Encoding UTF8 (Join-Path $ConfigDir "RustDesk2.toml")
$Exe = "${{env:ProgramFiles}}\\RustDesk\\rustdesk.exe"
if (Test-Path $Exe) {{
	& $Exe --option custom-rendezvous-server $HostName | Out-Null
	& $Exe --option relay-server $HostName | Out-Null
	& $Exe --option key $Key | Out-Null
}}
Write-Host "RustDesk is configured for $HostName"
"""
    (config.paths.www / "install.ps1").write_text(script, encoding="utf-8")


def asset_rows(manifest: dict) -> str:
    assets = manifest.get("assets", [])
    if not assets:
        return "<p>No client packages are mirrored yet. Run <code>./self_host.py mirror all</code>.</p>"
    rows = []
    for asset in assets:
        name = html.escape(asset["name"])
        label = html.escape(asset.get("platform", "package"))
        sha = html.escape(asset.get("sha256", ""))
        size_mb = asset.get("size", 0) / 1024 / 1024
        rows.append(
            f'<tr><td>{label}</td><td><a href="/packages/{name}">{name}</a></td>'
            f"<td>{size_mb:.1f} MB</td><td><code>{sha[:16]}</code></td></tr>"
        )
    return "<table><thead><tr><th>Platform</th><th>File</th><th>Size</th><th>SHA-256</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def android_apk(manifest: dict) -> str:
    apks = [asset["name"] for asset in manifest.get("assets", []) if asset["name"].endswith(".apk")]
    for apk in apks:
        if "aarch64" in apk:
            return apk
    return apks[0] if apks else ""


def android_link(manifest: dict) -> str:
    apk = android_apk(manifest)
    if not apk:
        return "Run <code>./self_host.py mirror android</code> to add a local APK."
    escaped = html.escape(apk)
    return f'<a href="/packages/{escaped}">{escaped}</a>'


def write_index(config: AppConfig, *, url: UrlInfo, key: str) -> None:
    manifest = read_manifest(config)
    display_key = key or "not generated yet; run ./self_host.py setup"
    escaped_key = html.escape(display_key)
    escaped_host = html.escape(url.host)
    escaped_base = html.escape(url.https)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RustDesk Self-Hosted</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }}
body {{ margin: 0; line-height: 1.45; background: #f6f7f9; color: #15171a; }}
main {{ max-width: 980px; margin: 0 auto; padding: 32px 18px 56px; }}
section {{ margin-top: 24px; }}
.panel {{ background: white; border: 1px solid #d9dde3; border-radius: 8px; padding: 18px; }}
h1 {{ margin: 0 0 8px; font-size: 2rem; }}
h2 {{ margin: 0 0 12px; font-size: 1.25rem; }}
code, pre {{ background: #eef1f5; border-radius: 6px; }}
code {{ padding: 2px 5px; }}
pre {{ padding: 12px; overflow: auto; }}
button {{ border: 1px solid #9aa4b2; border-radius: 6px; background: #fff; padding: 7px 10px; cursor: pointer; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ border-bottom: 1px solid #d9dde3; padding: 8px; text-align: left; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
@media (prefers-color-scheme: dark) {{
	body {{ background: #101214; color: #eef1f5; }}
	.panel {{ background: #171a1f; border-color: #303640; }}
	code, pre {{ background: #242a32; }}
	button {{ background: #171a1f; color: #eef1f5; border-color: #596273; }}
}}
</style>
</head>
<body>
<main>
<h1>RustDesk Self-Hosted</h1>
<p>Use this server for RustDesk ID and relay. No external services are required after packages are mirrored.</p>

<section class="grid">
<div class="panel"><h2>Server</h2><p>ID server: <code>{escaped_host}</code></p><p>Relay server: <code>{escaped_host}</code></p></div>
<div class="panel"><h2>Key</h2><pre id="key">{escaped_key}</pre><button data-copy="#key" data-label="Copy key">Copy key</button></div>
</section>

<section class="panel">
<h2>Linux and macOS</h2>
<pre id="install-sh">curl -fsSL {escaped_base}/install.sh | sh</pre>
<button data-copy="#install-sh" data-label="Copy command">Copy command</button>
</section>

<section class="panel">
<h2>Windows PowerShell</h2>
<pre id="install-ps">iwr {escaped_base}/install.ps1 -UseBasicParsing | iex</pre>
<button data-copy="#install-ps" data-label="Copy command">Copy command</button>
</section>

<section class="panel">
<h2>Android</h2>
<p>Download the mirrored APK from this server: {android_link(manifest)}</p>
<p>Install it, then open RustDesk settings and set ID server <code>{escaped_host}</code>, relay server <code>{escaped_host}</code>, and key <code>{escaped_key}</code>.</p>
</section>

<section class="panel">
<h2>Manual Config</h2>
<pre id="toml">{html.escape(rustdesk_toml(url, key=key))}</pre>
<button data-copy="#toml" data-label="Copy config">Copy config</button>
</section>

<section class="panel">
<h2>Mirrored Packages</h2>
{asset_rows(manifest)}
</section>
</main>
<textarea id="copy-buffer" style="position:fixed;left:-9999px;top:0"></textarea>
<script>
function websocketUrl(path) {{
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path;
}}
async function copyText(text) {{
  if (navigator.clipboard && window.isSecureContext) {{
    await navigator.clipboard.writeText(text);
    return;
  }}
  const box = document.getElementById("copy-buffer");
  box.value = text;
  box.focus();
  box.select();
  document.execCommand("copy");
}}
document.querySelectorAll("button[data-copy]").forEach((button) => {{
  button.addEventListener("click", async () => {{
    const target = document.querySelector(button.dataset.copy);
    await copyText(target.innerText.trim());
    const label = button.dataset.label || button.innerText;
    button.innerText = "Copied";
    setTimeout(() => button.innerText = label, 1000);
  }});
}});
</script>
</body>
</html>
"""
    (config.paths.www / "index.html").write_text(page, encoding="utf-8")


def write_site(config: AppConfig, *, url: UrlInfo) -> None:
    key = public_key(config)
    config.paths.www.mkdir(parents=True, exist_ok=True)
    config.paths.packages.mkdir(parents=True, exist_ok=True)
    if not config.paths.manifest.exists():
        write_manifest(config, release_tag=None, assets=[])
    write_install_sh(config, url=url, key=key)
    write_install_ps1(config, url=url, key=key)
    write_index(config, url=url, key=key)


def caddy_block(config: AppConfig, *, url: UrlInfo) -> str:
    return f"""
{config.caddy_begin}
{url.http} {{
	redir {url.https}{{uri}} permanent
}}

{url.https} {{
	encode zstd gzip
	root * {config.paths.www}
	file_server
}}
{config.caddy_end}
""".strip()


def remove_managed_block(content: str, *, config: AppConfig) -> str:
    kept = []
    skipping = False
    for line in content.splitlines():
        if line == config.caddy_begin:
            skipping = True
            continue
        if line == config.caddy_end:
            skipping = False
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip()


def update_caddy(config: AppConfig, *, url: UrlInfo) -> None:
    require_cmd("caddy")
    config.paths.caddyfile.parent.mkdir(parents=True, exist_ok=True)
    existing = config.paths.caddyfile.read_text(encoding="utf-8") if config.paths.caddyfile.exists() else ""
    content = remove_managed_block(existing, config=config) + "\n\n" + caddy_block(config, url=url) + "\n"
    config.paths.caddyfile.write_text(content, encoding="utf-8")
    run(["caddy", "fmt", "--overwrite", str(config.paths.caddyfile)])
    run(["caddy", "validate", "--config", str(config.paths.caddyfile)])
    result = run(["caddy", "reload", "--config", str(config.paths.caddyfile)], check=False, capture=True)
    if result.returncode != 0:
        run(["caddy", "start", "--config", str(config.paths.caddyfile)])


def start(config: AppConfig, *, raw_url: str) -> None:
    url = normalize_url(raw_url)
    require_cmd("tmux")
    stop(config)
    ensure_server_binaries(config)
    check_ports_free(config)
    config.paths.server_data.mkdir(parents=True, exist_ok=True)
    tmuxnew(config.sessions.hbbs, cwd=config.paths.server_data, command=[config.paths.hbbs, "-r", f"{url.host}:21117"])
    tmuxnew(config.sessions.hbbr, cwd=config.paths.server_data, command=[config.paths.hbbr])
    wait_for_public_key(config)
    write_site(config, url=url)
    update_caddy(config, url=url)
    log(f"started RustDesk at {url.https}")


def platform_matches(name: str, selected: str) -> bool:
    if selected == "all":
        return any(platform_matches(name, item) for item in ("windows", "macos", "linux", "android"))
    if selected == "windows":
        return name.endswith((".exe", ".msi")) and "x86_64" in name
    if selected == "macos":
        return name.endswith(".dmg") and ("x86_64" in name or "aarch64" in name)
    if selected == "linux":
        return name.endswith((".deb", ".AppImage", ".rpm", ".pkg.tar.zst")) and ("x86_64" in name or "aarch64" in name)
    if selected == "android":
        return name.endswith(".apk") and ("aarch64" in name or "universal" in name)
    die(f"unknown platform: {selected}")


def platform_label(name: str) -> str:
    if name.endswith((".exe", ".msi")):
        return "Windows"
    if name.endswith(".dmg"):
        return "macOS"
    if name.endswith(".apk"):
        return "Android"
    if name.endswith((".deb", ".AppImage", ".rpm", ".pkg.tar.zst")):
        return "Linux"
    die(f"unknown package platform for {name}")


def client_asset_from_file(path: Path) -> ClientAsset:
    return ClientAsset(
        name=path.name,
        platform=platform_label(path.name),
        size=path.stat().st_size,
        sha256=sha256_file(path),
    )


def manifest_dict(*, release_tag: str | None, assets: list[ClientAsset]) -> dict:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release": release_tag,
        "assets": [asset.__dict__ for asset in sorted(assets, key=lambda item: (item.platform, item.name))],
    }


def write_package_index(config: AppConfig, *, assets: list[ClientAsset]) -> None:
    names = [asset.name for asset in sorted(assets, key=lambda item: item.name)]
    (config.paths.packages / "packages.txt").write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")


def write_manifest(config: AppConfig, *, release_tag: str | None, assets: list[ClientAsset]) -> None:
    config.paths.packages.mkdir(parents=True, exist_ok=True)
    config.paths.manifest.write_text(json.dumps(manifest_dict(release_tag=release_tag, assets=assets), indent=2) + "\n", encoding="utf-8")
    write_package_index(config, assets=assets)


def mirrored_assets(config: AppConfig) -> list[ClientAsset]:
    if not config.paths.packages.exists():
        return []
    return [
        client_asset_from_file(path)
        for path in config.paths.packages.iterdir()
        if path.is_file() and path.name not in ("manifest.json", "packages.txt")
    ]


def mirror(config: AppConfig, *, platform_name: str, raw_url: str) -> None:
    release = latest_release(config.client_repo)
    config.paths.packages.mkdir(parents=True, exist_ok=True)
    for asset in release.get("assets", []):
        name = asset["name"]
        if not platform_matches(name, platform_name):
            continue
        path = config.paths.packages / name
        if not path.exists() or path.stat().st_size != asset.get("size", -1):
            log(f"mirroring {name}")
            download(asset["browser_download_url"], destination=path, expected_sha256=asset_sha256(asset))
        else:
            verify_sha256(path, expected_sha256=asset_sha256(asset))
    assets = mirrored_assets(config)
    selected = [asset for asset in assets if platform_matches(asset.name, platform_name)]
    if not selected:
        die(f"no client assets mirrored for platform {platform_name}")
    write_manifest(config, release_tag=release.get("tag_name"), assets=assets)
    write_site(config, url=normalize_url(raw_url))
    log(f"mirrored {len(selected)} client package(s)")


def unmirror(config: AppConfig, *, platform_name: str, raw_url: str) -> None:
    config.paths.packages.mkdir(parents=True, exist_ok=True)
    previous_release = read_manifest(config).get("release")
    for path in list(config.paths.packages.iterdir()):
        if path.name != "manifest.json" and platform_matches(path.name, platform_name):
            log(f"removing {path.name}")
            path.unlink()
    assets = mirrored_assets(config)
    write_manifest(config, release_tag=previous_release, assets=assets)
    write_site(config, url=normalize_url(raw_url))
    log(f"{len(assets)} mirrored package(s) remain")


def add_url_argument(parser: argparse.ArgumentParser, *, default: str) -> None:
    parser.add_argument("url", nargs="?", default=default, help=f"public URL (default: {default})")


def parse_args(config: AppConfig):
    parser = argparse.ArgumentParser(description="Self-host RustDesk without Docker.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("setup", "redeploy", "start"):
        command_parser = sub.add_parser(command)
        add_url_argument(command_parser, default=config.default_url)
    sub.add_parser("stop")
    for command in ("mirror", "unmirror"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("platform", nargs="?", default="all", choices=PLATFORMS, help="platform to manage (default: all)")
        add_url_argument(command_parser, default=config.default_url)
    return parser.parse_args()


def main() -> None:
    config = default_config()
    args = parse_args(config)
    if args.command == "setup":
        start(config, raw_url=args.url)
    elif args.command == "redeploy":
        start(config, raw_url=args.url)
    elif args.command == "start":
        start(config, raw_url=args.url)
    elif args.command == "stop":
        stop(config)
    elif args.command == "mirror":
        mirror(config, platform_name=args.platform, raw_url=args.url)
    elif args.command == "unmirror":
        unmirror(config, platform_name=args.platform, raw_url=args.url)
    else:
        die(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()

# RustDesk Self-Hosting

This directory self-hosts RustDesk OSS without Docker. It runs the official
`hbbs` and `hbbr` server binaries in tmux and lets Caddy serve a static local
install page.

Default URL:

```sh
https://rustdesk.pinky.lilf.ir
```

Override it by passing a URL:

```sh
./self_host.py setup https://desk.example.lan
```

## Requirements

Install these host tools first:

```sh
sudo apt-get update
sudo apt-get install -y python3 tmux caddy aria2 curl unzip iproute2
```

The script downloads official RustDesk release assets with the current shell
environment. Proxy variables such as `ALL_PROXY`, `HTTPS_PROXY`, `NO_PROXY`,
`npm_config_proxy`, and lowercase variants are not hardcoded, but they are
passed through to tmux sessions when already present.

Downloads prefer `aria2c` with 8 parallel connections/splits and resume support,
then fall back to resumable `curl`. When GitHub publishes a `sha256:` digest for
an asset, the script verifies the downloaded file before using or mirroring it.

## Commands

```sh
./self_host.py setup [url]          # stop, fetch server binaries if needed, start, update Caddy
./self_host.py redeploy [url]       # restart and regenerate generated site/install files
./self_host.py start [url]          # stop existing sessions, then start production sessions
./self_host.py stop                 # stop RustDesk tmux sessions
./self_host.py mirror [platform]    # mirror client installers, default platform is all
./self_host.py unmirror [platform]  # remove mirrored client installers to save space
```

Supported mirror platforms are `all`, `windows`, `macos`, `linux`, and
`android`.

`dev-start` is intentionally not implemented because this deployment runs
official release binaries; there is nothing useful to hot reload.

## Ports

The script checks that these ports are free before starting:

```text
TCP: 21115, 21116, 21117, 21118, 21119
UDP: 21116
```

Open them in the firewall for clients:

```sh
sudo ufw allow 21115:21119/tcp
sudo ufw allow 21116/udp
```

Caddy handles only the static install page over HTTP/HTTPS. RustDesk clients
connect directly to the RustDesk ports above.

## Client Install Page

After setup, open:

```sh
https://rustdesk.pinky.lilf.ir
```

The page is intranet-friendly: it has no external fonts, captcha, analytics,
Firebase, CDN assets, or package-manager calls. Copy buttons work on HTTPS and
fall back to a hidden textarea for plain HTTP/local use.

Linux/macOS command:

```sh
curl -fsSL https://rustdesk.pinky.lilf.ir/install.sh | sh
```

On macOS, the script downloads the mirrored DMG for the local architecture,
mounts it, installs the app into `/Applications`, and writes the RustDesk
server config.

Windows PowerShell command:

```powershell
iwr https://rustdesk.pinky.lilf.ir/install.ps1 -UseBasicParsing | iex
```

Android:

1. Run `./self_host.py mirror android` or `./self_host.py mirror all`.
2. Open the page from the Android device.
3. Download the mirrored APK from this server.
4. Install it manually.
5. In RustDesk network/server settings, set the ID server, relay server, and key
   shown on the page.

The installers and page write/apply this OSS client config:

```toml
rendezvous_server = 'rustdesk.pinky.lilf.ir'
nat_type = 1
serial = 0

[options]
custom-rendezvous-server = 'rustdesk.pinky.lilf.ir'
relay-server = 'rustdesk.pinky.lilf.ir'
key = '<server public key>'
```

## Mirror And Blackout Use

Mirror client installers while internet access is available:

```sh
./self_host.py mirror all
```

This stores official RustDesk client installers under:

```text
.self_host/www/packages/
```

It also writes `.self_host/www/packages/manifest.json`, which local installers
use during a network blackout. The mirror command intentionally mirrors client
installers only. Server binaries are fetched separately by `setup` when absent.

Remove mirrored packages when space matters:

```sh
./self_host.py unmirror all
```

## Caddy

`self_host.py` manages this block in `~/Caddyfile`:

```caddy
# BEGIN RustDesk self-host
http://rustdesk.pinky.lilf.ir {
	redir https://rustdesk.pinky.lilf.ir{uri} permanent
}

https://rustdesk.pinky.lilf.ir {
	encode zstd gzip
	root * /home/ubuntu/base/RustDesk/.self_host/www
	file_server
}
# END RustDesk self-host
```

Run `./self_host.py setup` or `./self_host.py redeploy` after changing the
target URL so the Caddy block and generated installers stay aligned.

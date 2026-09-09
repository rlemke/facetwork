# fleet-agent supervisor templates

> Kept OUTSIDE `scripts/lib/` on purpose. The `fw` dispatcher treats the
> filesystem as its command registry — every file under `scripts/lib/<group>/`
> becomes `fw <group> <command>` — so templates placed there turn into fake
> commands that are not executable and have no shebang.

The fleet agent needs a supervisor: `fw fleet agent watch` is meant to be
permanent, and its own watchdog deliberately hard-exits when a reconcile wedges
(`FW_FLEET_AGENT_WATCHDOG_SECONDS`) so that something restarts it with fresh
state. Without a supervisor that self-kill simply ends the agent, and the host
then sits at whatever `fleet_config` version it last applied — silently, because
`fw fleet status` counts REGISTRATIONS and a host that stopped reconciling still
has its old runners registered.

These are the Linux files, installed on atopnuc01 2026-09-08. The macOS hosts
(MaxPro, server3) use hand-written launchd equivalents in `~/.facetwork/`.


## Provisioning a brand-new Ubuntu host

`setup-ubuntu-fleet-host.sh` does the whole thing from a fresh install — packages,
SSH, remote desktop, Docker + the plain-HTTP registry, the repo and venv, the
per-host fleet config, and this agent. It is idempotent and has a `--dry-run`.

```bash
curl -fsSLO https://raw.githubusercontent.com/rlemke/facetwork/main/docs/operations/fleet-agent/setup-ubuntu-fleet-host.sh
bash setup-ubuntu-fleet-host.sh --dry-run          # review the plan first
bash setup-ubuntu-fleet-host.sh --ssh-key 'ssh-ed25519 AAAA...'
```

Options: `--group` (default `runner`), `--data-dir`, `--infra-host`,
`--rdp remote-login|xrdp|none`, `--ssh-key`, `--no-join`.

⚠️ **The default group is `runner` on purpose.** `heavy` opts the host into the
OSM tier, where a single europe cut has peaked at **18.9 GB RSS**. On a machine
that cannot take that, a mis-set group is an OOM kill, not a slow run.

⚠️ **Choosing the RDP mode is a real decision, not a preference.** GNOME ships two
services. *Remote Login* (system unit) needs no local session — right for a
headless server — but hands the client over using RDP **server redirection**, and
the Mac "Windows App" client does not follow it: it reports *"the credentials did
not work"* **after** authentication has already succeeded. Use a
redirection-capable client (FreeRDP: `sdl-freerdp /v:host /u:user`), or pick
`--rdp xrdp`, which serves its own session with no handover and works with the
Microsoft clients. *Desktop Sharing* (user unit) is the third option and is not
scripted: it shares the existing session, needs someone logged in locally, and
keeps its password in the GNOME keyring, which autologin typically leaves locked.

Validated on atopnuc01 (Ubuntu 26.04.1 LTS, python 3.14.4, docker.io 29.1.3,
docker-compose-v2 2.40.3).

## Install (Linux / systemd) — agent only

```bash
install -m 755 docs/operations/fleet-agent/fleet-agent-watch.linux.sh \
        ~/.facetwork/fleet-agent-watch.sh
sudo install -m 644 docs/operations/fleet-agent/facetwork-fleet-agent.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now facetwork-fleet-agent
```

Edit `User=`/`Group=`/paths in the unit if the account is not `ralph_lemke`.

Per-host identity comes from `.env.fleet.override` in the repo
(`FW_SERVER_GROUP`, `FW_DATA_DIR`) — NOT from this wrapper, so there is one
place per machine that says what it is.

## Why each non-obvious setting is there

Each one pins a failure this fleet has actually had:

* **`Restart=always` + `StartLimitIntervalSec=0`** — the watchdog's hard exit is
  a feature. systemd's defaults (`StartLimitBurst=3` in 1 min, as `docker.service`
  itself uses) would treat repeated self-kills as a crash loop and STOP the unit
  permanently, turning a self-healing design into a dead one.
* **`KillMode=process`** — runners are detached containers that must OUTLIVE the
  agent. Stopping the agent must never stop the fleet; the macOS hosts print
  "stopped (runners left running)" for the same reason.
* **The `docker info` poll in the wrapper** — `After=docker.service` only orders
  startup. dockerd accepts connections seconds later, so at boot the first
  reconcile would otherwise fail against a socket that is not ready yet.
* **`SupplementaryGroups=docker`** — without it every reconcile fails with a
  permission error on the socket, which reads like a missing binary.
* **The `/etc/hosts` staleness warning** — the agent connects to the infra host
  by NAME (`afl-mongodb`), which resolves through the host's `/etc/hosts`, and
  nothing running as this user can maintain that file. When the infra host's
  DHCP lease moves, every reconcile fails against an address nobody answers.
  Measured 2026-09-08 after a power outage: server3 moved .67 -> .115 and both
  other hosts kept the old entry. ⚠️ The agent reported **"up to date (v200)"**
  throughout, because it compares the config VERSION, which had not changed —
  so the wrapper says it instead, and prints the exact `sed` to fix it.

## ⚠️ Kickstart after a `git pull`

A running agent keeps the code it started with. MaxPro's had been up 10 days and
was still running pre-drift-detection code, so it could not have healed itself
no matter how long it waited. After deploying agent changes:

```bash
sudo systemctl restart facetwork-fleet-agent            # Linux
launchctl kickstart -k gui/$(id -u)/com.facetwork.fleet-agent   # macOS
```

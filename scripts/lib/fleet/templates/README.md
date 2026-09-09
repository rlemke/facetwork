# fleet-agent supervisor templates

The fleet agent needs a supervisor: `fw fleet agent watch` is meant to be
permanent, and its own watchdog deliberately hard-exits when a reconcile wedges
(`FW_FLEET_AGENT_WATCHDOG_SECONDS`) so that something restarts it with fresh
state. Without a supervisor that self-kill simply ends the agent, and the host
then sits at whatever `fleet_config` version it last applied — silently, because
`fw fleet status` counts REGISTRATIONS and a host that stopped reconciling still
has its old runners registered.

These are the Linux files, installed on atopnuc01 2026-09-08. The macOS hosts
(MaxPro, server3) use hand-written launchd equivalents in `~/.facetwork/`.

## Install (Linux / systemd)

```bash
install -m 755 scripts/lib/fleet/templates/fleet-agent-watch.linux.sh \
        ~/.facetwork/fleet-agent-watch.sh
sudo install -m 644 scripts/lib/fleet/templates/facetwork-fleet-agent.service \
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

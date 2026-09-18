# Operating a fleet without sudo

**Privilege establishes a host. It must never operate one.**

Every `sudo` this fleet has needed in day-to-day work was a *provisioning* step
that had not been folded into provisioning. That is survivable at seven machines
and disqualifying at a hundred: a per-host interactive password is not an
operation, it is an outage waiting for someone's attention.

This document is the inventory — every root-requiring action, why it needs root,
where it belongs, and what it costs if you leave it for later — plus the two
architectural options that shrink even the provisioning privilege, and how the
whole thing looks at 10 / 100 / 1000 machines.

---

## 1. The rule

> One idempotent privileged step per host, ever. After it, **no day-2 operation
> may require root.** If one does, that is a provisioning bug, not an ops task.

Two corollaries that are easy to get wrong:

- **"It only needs sudo occasionally" is not a mitigation.** The occasional case
  is the incident: 3 a.m., one host misbehaving, and the fix is gated on a human
  who can type a password on that specific machine.
- **A privileged step that is not idempotent is not provisioning**, it is a
  ritual. It must be safe to re-run on an already-provisioned host, because that
  is how you repair drift without knowing the host's history.

---

## 2. Inventory — every root-requiring action on this fleet

Measured, not hypothetical: each row cost real time here.

| # | Action | Why it needs root | Belongs in | Cost of deferring it |
|---|---|---|---|---|
| 1 | Install the agent's systemd unit | writes `/etc/systemd/system` | provision | the agent cannot run at all |
| 2 | **`systemctl enable`** the unit | `manage-unit-files` | provision | host works, then **silently vanishes at its next reboot**. Measured on atopnuc02: active but `disabled`, and nothing reported it |
| 3 | **Install the polkit rule** | writes `/etc/polkit-1/rules.d` | provision | **every** agent restart needs an interactive sudo — the toil that prompted this document |
| 4 | docker group membership | `usermod -aG docker` | provision — **already done** by the setup script, which also reports a PEND state because the group does not apply to the shell that just made the change | every `docker` call needs sudo, so nothing about containers is scriptable |
| 5 | `insecure-registries` + `systemctl restart docker` | `/etc/docker/daemon.json` | provision | cannot pull the fleet image |
| 6 | Mount the data disk (`/etc/fstab`) | writes `/etc/fstab` | provision | no scratch; heavy work fails or fills the root fs |
| 7 | **Pre-create bind-mount sources** | must be owned by the runtime user | provision — partially done (`$DATA_DIR` only) | ⚠️ **Docker invents a missing bind source as `root:root`.** Measured: 23 such directories across four hosts — `~/fw_handlers` among them — which broke `fw install domain` weeks later and needed a `chown` sweep |
| 8 | ~~`/etc/hosts` entries for `afl-*`~~ | ~~file ownership~~ | **eliminated** | — the server catalog resolves names at startup, so the requirement was *designed away* rather than automated |

Row 8 is the best outcome available and worth calling out as a pattern: **the
cheapest privileged step is the one you delete.** Before automating a root
action, ask whether the need can be removed. `/etc/hosts` drift used to require a
`sudo sed` on every host; the catalog made that impossible to need.

---

## 3. What day-2 must never require

The list that defines "done". Each is something we have actually needed on a live
host, and each must work over plain ssh as the ordinary runtime user:

| Day-2 operation | Unprivileged mechanism |
|---|---|
| restart the agent (deploy new code) | polkit rule (§4 option A) or `docker restart` (option B) |
| stop / start the agent (park a host) | same |
| enable / disable at boot (join / leave) | polkit rule **including `manage-unit-files`** |
| pull new code | `git pull` as the user |
| inspect, restart, recreate runners | docker group membership |
| install or refresh a scheduled job | user crontab / launchd — already unprivileged |
| read logs | journal access for own units, or the agent's own log files |

⚠️ **`mask` / `unmask` stay privileged on purpose.** Masking is how a unit is made
unstartable; that belongs with the administrator, not with routine ops. The
polkit rule this repo installs deliberately excludes those verbs.

---

## 4. Two ways to make day-2 unprivileged

Both work. They differ in what single privilege they depend on.

### Option A — system unit + a narrowly scoped polkit rule *(what this fleet runs)*

`fw fleet allow-restart` installs a rule permitting **one user** to
start/stop/restart/reload **and** enable/disable/reenable **one unit**:

```bash
sudo fw fleet allow-restart      # once, at provision
fw fleet allow-restart --check   # verify as the USER, never as root
```

- ✅ The agent keeps every systemd property you want: boot ordering,
  `RequiresMountsFor`, restart backoff, journal integration.
- ✅ The grant is auditable and tiny — one user, one unit, an enumerated verb list.
- ⚠️ Verify **as the user**. root is always permitted, so a check run under sudo
  proves nothing about the rule.
- ⚠️ Boot persistence is a **different polkit action** (`manage-unit-files`) from
  restart. A rule covering only restart lets a host pass the restart check and
  still never come back from a reboot — exactly what happened on atopnuc02, which
  is why `--check` now reports the two separately.

### Option B — run the agent as a container

Give the agent `restart: always` and a mounted docker socket. "Restart the agent"
becomes `docker restart facetwork-fleet-agent`.

- ✅ **No systemd, no polkit, no unit file.** The only privilege needed anywhere is
  docker-group membership, which the host needs regardless.
- ✅ The agent updates the way everything else does — pull an image — so "the
  daemon is running three-day-old code" stops being possible.
- ⚠️ The docker socket is root-equivalent. This grants no *new* privilege (the
  runtime user is already in the docker group), but it does mean the agent's
  container is as trusted as root on that box. Say so out loud rather than
  discovering it in a review.
- ⚠️ You inherit Docker's start ordering instead of systemd's, so a host whose
  data volume mounts late needs the wait handled *inside* the agent.

**Recommendation.** Option A for a fleet of machines you administer directly;
Option B once provisioning is image-based, because it removes the last per-host
file you have to place as root.

### Option C — systemd *user* unit + linger

Worth knowing, not recommended here. `loginctl enable-linger <user>` (root, once)
lets the user own `systemctl --user` entirely: no polkit rule at all, start/stop/
enable all unprivileged.

- ✅ Fewer privileged artifacts than A: one linger flag instead of a rules file.
- ⚠️ Linger is off on these hosts, and user units do not get system-level
  ordering (`After=docker.service`, `RequiresMountsFor`) — the agent would have to
  wait for Docker and for mounts itself.
- ⚠️ A user session ending can still surprise you; linger is exactly what stops
  that, so the flag is load-bearing and easy to forget on host #300.

---

## 5. The provisioning step, in full

One script, idempotent, non-interactive. On this fleet:
`docs/operations/fleet-agent/setup-ubuntu-fleet-host.sh`.

It must do **all** of the following, so that nothing is left for later:

1. add the runtime user to the `docker` group (and say that a re-login is needed
   for it to take effect in the current shell);
2. write `/etc/docker/daemon.json` (insecure registry) and restart Docker;
3. create every bind-mount source **as the runtime user** — before any container
   can invent it as root (§2 row 7);
4. mount the data disk by **UUID**, with `nofail` and a device timeout, ordered
   before Docker (see [mounting-a-data-disk.md](mounting-a-data-disk.md));
5. install the agent unit and `enable --now` it;
6. install the polkit rule (§4 option A);
7. verify: restart without sudo, and `is-enabled` = enabled.

Requirements on the script itself, because these are what make it usable at
scale rather than by hand:

- **Idempotent** — safe on an already-provisioned host; that is how drift is
  repaired.
- **Non-interactive** — `sudo -n`, no prompts, so it runs from cloud-init or a
  config-management tool with no TTY.
- **Meaningful exit codes** — 0 provisioned, non-zero with a reason, never a
  silent partial. ⚠️ A provisioning script that half-succeeds is worse than one
  that fails: the host then looks joined and behaves oddly.
- **Verifying** — it must end by *proving* day-2 is unprivileged, not by asserting
  it. See §6.

---

## 6. Prove it, don't assert it

A provisioning run that claims success while day-2 still needs root is the same
class of defect as [the probe that cannot
fail](diagnosis-field-notes.md#1-the-probe-that-cannot-fail). So the check runs
**as the user**, exercises the real verbs, and reports the two dimensions
separately:

```bash
fw fleet allow-restart --check
#   verify: ✓ restarted facetwork-fleet-agent.service with no sudo
#   boot:   ✓ enabled (survives a reboot)
```

Exit **0** only when both hold; non-zero names which one failed. Run it from the
provisioning host across the fleet — a one-line sweep tells you which machines
would need a human:

```bash
for h in $(fw fleet servers --names); do
  printf '%-14s %s\n' "$h" "$(ssh "$h" 'cd ~/facetwork && ./fw fleet allow-restart --check >/dev/null 2>&1 && echo ok || echo NEEDS-ATTENTION')"
done
```

⚠️ Note what this check does **not** cover, so nobody reads it as broader than it
is: docker-group membership, registry reachability, bind-source ownership and the
data mount are provisioning concerns verified by `fw install check`, not by this.

---

## 7. Scaling: 10 → 100 → 1000

The privileged step does not get cheaper by being scripted; it gets cheaper by
being **moved earlier**.

| Scale | How provisioning should happen | Day-2 |
|---|---|---|
| ~10 | Run the setup script over ssh, once per host. One password each, then never again. | plain ssh, no root |
| ~100 | Config management (Ansible/Salt) invoking the *same* script. It is already idempotent and non-interactive, which is the only reason this works. | unchanged |
| 1000+ | **Bake it into the machine image** (Packer/AMI/cloud-init user-data). A host boots already provisioned; nobody ever holds root on an individual machine. | unchanged |

The important property: **all three run the same script.** If the 1000-machine
path needs a different mechanism from the 10-machine path, the 10-machine path is
not really provisioning — it is manual work wearing a script's clothes.

At the top tier, prefer Option B (§4): an image that boots with a docker daemon
and a `restart: always` agent container needs **no per-host file placed as root
at all**, and the agent updates by image pull like everything else.

---

## 8. What this fleet still owes

Checked against the script rather than assumed — it already covers more than
memory suggested (docker group, `$DATA_DIR`, unit install, `enable --now`,
daemon.json, and a verification pass with a correct PEND state for the
not-yet-effective group change). Three genuine gaps remain, and they are exactly
the sudo we paid by hand this week:

- **The polkit rule is not installed** (§5 item 6) — so the very first agent
  restart after provisioning needs a password.
- **Bind-source pre-creation stops at `$DATA_DIR`** (§5 item 3). `~/fw_handlers`
  and the per-runner scratch subdirectories are left for Docker to invent as
  root. That is the 23-directory `chown` sweep, waiting to happen again.
- **Nothing verifies the agent is `enable`d.** The verification pass checks
  `sleep.target` is masked but never asks whether the agent survives a reboot —
  which is precisely how atopnuc02 ran for days, active and disabled, with
  nothing reporting it.
- The installed polkit rules predate the enable/disable clause, so parking or
  rejoining a host still needs one sudo until `sudo fw fleet allow-restart` is
  re-run.
- macOS hosts (MaxPro, server3) run the agent under **launchd**, where the owner
  can already `launchctl kickstart -k gui/$UID/com.facetwork.fleet-agent` with no
  sudo. They need no equivalent of §4, and `allow-restart` refuses on them and
  says so.

# Mounting a data disk on a fleet host

A fleet host often has a second disk for scratch/cache. Mounting it durably has
two failure modes that are invisible until a reboot, and both have bitten this
fleet.

## 1. Name the filesystem by UUID, never by device path

**`/dev/sdb`, `/dev/nvme1n1p4` and friends are not stable identifiers.** They are
assigned in probe order, which depends on controller enumeration and can differ
between the initramfs and the booted system, between kernels, and between boots.

Measured on beelink01: the root filesystem (UUID `fad5c9ab-…`) is
`nvme0n1p2` in the booted system, but every boot journal shows the initramfs
mounting that same UUID as **`nvme1n1p2`** — the two identical 931 GB NVMe drives
swap names depending on who is looking. A host with `sda`/`sdb` (macmini01) has
the same exposure for the same reason.

An fstab entry naming a device path therefore points at *a* disk, not at *your*
disk. When the order shifts, the mount fails — or, far worse, succeeds against
the wrong filesystem.

Get the UUID:

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,UUID
```

## 2. `nofail`, or a missing disk stops the boot

Without `nofail`, systemd treats the mount as required by `local-fs.target`. A
disk that is absent, slow, or renamed drops the machine to an emergency shell —
on a headless fleet host, that is indistinguishable from "it didn't come back".

`x-systemd.device-timeout` bounds how long boot waits for it rather than using
the 90-second default.

The entry:

```
UUID=<uuid>  /srv/afl_data  ext4  defaults,nofail,x-systemd.device-timeout=30  0  2
```

Mount under `/srv` or `/mnt`, **not** inside `$HOME`. A mount over a home
directory hides whatever is already there, and the hidden copy is still on the
root filesystem consuming space that `df` now attributes elsewhere.

## 3. Order Docker after the mount

This is the fleet-specific trap and the one that costs the most time.

If a container bind-mounts from the data disk and **Docker starts before the
mount completes**, the bind source resolves to the empty directory *underneath*
the mountpoint on the root filesystem. Containers then run happily, writing to
the wrong disk, and nothing reports it. Measured here: a udisks session mount
appeared 4.5 minutes after Docker had already started.

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/10-data-disk.conf >/dev/null <<'EOF'
[Unit]
RequiresMountsFor=/srv/afl_data
EOF
sudo systemctl daemon-reload
```

`RequiresMountsFor` makes Docker both *require* and *order after* that mount, so
a failed mount keeps Docker down rather than letting it write to the wrong place.
Down is loud; silently-wrong is not.

Compose's `create_host_path: false` on the bind is the same guard one level up —
it refuses to invent a missing source directory. Keep both.

## 4. The only proof is a reboot

`mount -a` succeeding proves the entry parses, not that it survives a boot — the
ordering and device-naming faults above only appear during startup. After
editing fstab:

```bash
sudo mount -a && findmnt /srv/afl_data   # entry is valid
sudo reboot
# then, once it is back:
findmnt /srv/afl_data && docker ps --filter name=facetwork-runner | wc -l
```

If the mount is absent after reboot, read `journalctl -b -1 -u srv-afl_data.mount`
(the unit name is the mountpoint with `/` → `-`) before changing anything.

## macOS hosts

The equivalent on server3 is Docker Desktop file sharing rather than fstab. An
external APFS volume that is attached but unmounted makes every bind-mounted
container fail to start with:

```
error while creating mount source path '/host_mnt/Volumes/afl_data': permission denied
```

Check `diskutil list` for the volume, then `sudo diskutil mount <diskNsM>`.
Unprivileged `diskutil mount` can fail silently where the privileged one
succeeds, so the lack of an error message is not evidence of a healthy disk.
Containers that exhausted their restart budget during the outage stay `Exited`
and need an explicit `docker start` — they do not self-recover.

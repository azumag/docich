# Soren91 one-shot PNG rejected-frame diagnostics

This operator path is separate from the public Actions evidence workflow. It
starts one five-minute manual Soren91 corner through the existing owner SSH
access and transfers only a selected run's already-captured diagnostic PNGs to
the operator's private Mac area. It adds no SSH credential, gateway allowlist,
daemon, or public endpoint.

## Start one trial

Run the reviewed helper on the production checkout using the already approved
operator SSH session:

```sh
python3 ops/vm_actions/soren91_private_frame_handoff.py start
```

The helper invokes the existing fixed five-minute manual runner with the
single supported profile `rejected_png_v1`. The request records that profile
in the manual reservation so a common-rotation queue retry retains it. At
creation, that one-shot authorization expires after 60 seconds. If rotation
cannot dispatch it in that window, the reservation remains visible for
explicit recovery, while later rotation ticks and manual retries refuse to
start diagnostics from the expired authorization. The manual runner checks the
deadline again immediately before entering the Soren lifecycle, after any
program-slot wait. If no exact owner started and GameSwitch is stable, rotation
releases that expired reservation and continues; uncertain ownership stays
parked for recovery. At execution time the Soren adapter scopes
`SOREN91_CAPTURE_FORMAT=png` and
`SOREN91_REJECT_FRAME_DIAGNOSTICS=1` to that request. It sends the fixed
`captureProfile` value through the existing authenticated local agent start
call; the agent adds the two variables only to the new renderer child. Neither
the VM credential EnvironmentFile nor the Mac agent's own environment is
edited. The temporary adapter environment is restored after the request, and
the renderer child exits with the five-minute corner.

An active renderer with different capture settings is left running and the
trial fails closed. The adapter does not restart it to turn diagnostics on.
Only a fresh renderer whose status confirms PNG plus rejected-frame diagnostics
is accepted for this profile.

## Read a selected run privately

Use an exact UUID from the owner-visible runtime state; the helper does not
select `latest` or traverse arbitrary paths. Through the same authorized
operator SSH access, stream just that run to a new 0700 Mac directory. Keep the
archive private, then verify and receive it locally:

```sh
umask 077
mkdir -m 700 /path/to/private/soren91-transfer
ssh <existing-operator-target> \
  'python3 /home/ubuntu/docich/ops/vm_actions/soren91_private_frame_handoff.py export <RUN-UUID>' \
  > /path/to/private/soren91-transfer/frames.tar
mkdir -m 700 /path/to/private/soren91-transfer/verified
python3 ops/vm_actions/soren91_private_frame_handoff.py receive <RUN-UUID> \
  --archive /path/to/private/soren91-transfer/frames.tar \
  --output /path/to/private/soren91-transfer/verified/capture
```

Replace `<existing-operator-target>` and `<RUN-UUID>` with values from the
already approved SSH session and selected trial. Do not add a new key, SSH
target, or port for this operation. The read helper follows no symlinks,
requires stable owner-only regular files and matching image/sidecar pairs, and
accepts no more than three PNGs, each at most 8 MiB. It emits only a bounded
tar stream with fixed relative paths. Local receipt checks the run UUID,
schema, PNG IHDR dimensions, and per-file SHA-256 before creating new 0700/0600
files. It rejects existing destinations and malformed or mixed-run archives.

Metadata distinguishes `currentPieceConfidence` from `boardConfidence`, keeps
the `fileMtimeMs` output-file timestamp explicit, and records capture-relative
`capturedAtMs`, `captureMs`, plus a fixed numeric canvas-geometry allowlist.
The transfer contains no runtime paths, credentials, logs, or arbitrary files.

The helper's fixture tests exercise archive validation and local private
receipt. Runtime SSH execution and Library storage remain a separate owner
operation after review and deployment; no image is published to GitHub Actions.

# Deploy runbook

This MVP is two services: the generator writes Bronze + `live.sqlite`; the
detector reads `live.sqlite` and opens/diagnoses incidents. Start them only
after the historical artifact and calibrated baseline exist.

## Fresh host

```bash
git clone <repo-url> /opt/nextwave
cd /opt/nextwave
./scripts/bootstrap.sh
```

`bootstrap.sh` deliberately regenerates history before T1 calibration. Do not
regenerate history while either live process is running: it deletes
`data/gold/live.sqlite`.

For an interactive demo, run these in separate terminals:

```bash
.venv/bin/python -m pipeline.generator.generate_live_stream --duration 0
.venv/bin/python -m agent_workflow.main --live
```

The generator starts at the next 12:00 UTC after `history_end`, avoiding the
known 02:00–07:00 UTC sparse-baseline artifact. Override only for controlled
tests with `--sim-start 2026-08-31T12:00:00Z`.

Bronze rotates at 50 MiB, retaining `events.jsonl.1`; set `BronzeStore(...,
max_bytes=0)` only for short debugging runs that need an unbounded log.

## systemd

The checked-in units assume the checkout is `/opt/nextwave` and runs as user
`nextwave`. Adjust those two values if needed, then install:

```bash
sudo install -D -m 0644 deploy/nextwave-live-generator.service /etc/systemd/system/nextwave-live-generator.service
sudo install -D -m 0644 deploy/nextwave-detector.service /etc/systemd/system/nextwave-detector.service
sudo install -d -m 0750 /etc/nextwave
sudoedit /etc/nextwave/nextwave.env
sudo systemctl daemon-reload
sudo systemctl enable --now nextwave-live-generator nextwave-detector
```

Put `OPENAI_API_KEY=...` in `/etc/nextwave/nextwave.env` when LLM diagnosis is
enabled. A missing or failing key leaves deterministic root alerts running and
marks diagnosis as insufficient evidence.

Useful checks:

```bash
systemctl status nextwave-live-generator nextwave-detector
journalctl -u nextwave-detector -f
```

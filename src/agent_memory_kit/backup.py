"""Demo backup and fresh-isolated-restore acceptance using Docker Compose."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT
POSTGRES_IMAGE = "postgres:17-alpine"


class BackupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(arguments: list[str], *, cwd: Path, stdout=None, timeout: int = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or b"").decode("utf-8", "replace")[-2000:]
        raise BackupError("Command failed without retry: " + detail)
    return result


def compose(root: Path, *arguments: str) -> list[str]:
    return ["docker", "compose", "--project-directory", str(root), "--file", str(root / "compose.yml"), *arguments]


def source_snapshot(root: Path) -> tuple[int, str]:
    count = int(run(
        compose(root, "exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM project_events"'),
        cwd=root,
    ).stdout.strip())
    hashes = run(
        compose(root, "exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "SELECT id, payload_hash FROM project_events ORDER BY id"'),
        cwd=root,
    ).stdout.decode("utf-8").strip()
    return count, hashes


def verify_manifest(manifest_path: Path) -> bool:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dump = manifest_path.parent / manifest["file"]
    return dump.is_file() and dump.stat().st_size == manifest["bytes"] and sha256(dump) == manifest["sha256"]


def acceptance(project_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise BackupError("Output directory must not already exist")
    output_dir.mkdir(parents=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    dump = output_dir / "agent-memory.dump"
    before_count, before_hashes = source_snapshot(project_dir)
    with dump.open("wb") as target:
        run(compose(project_dir, "exec", "-T", "db", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl'), cwd=project_dir, stdout=target)
    os.chmod(dump, 0o600)
    manifest = {
        "file": dump.name,
        "format": "postgresql-custom-v1",
        "bytes": dump.stat().st_size,
        "sha256": sha256(dump),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    if not verify_manifest(manifest_path):
        raise BackupError("New backup manifest does not match")
    tampered = dict(manifest)
    tampered["sha256"] = "0" * 64
    tampered_path = output_dir / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    if verify_manifest(tampered_path):
        raise BackupError("Tampered manifest was accepted")

    name = "agent-memory-restore-" + uuid.uuid4().hex[:12]
    receipt = {
        "status": "confirmed",
        "events": before_count,
        "backup_sha256": manifest["sha256"],
        "manifest_verified": True,
        "tamper_rejected": True,
    }
    try:
        run([
            "docker", "run", "--detach", "--rm", "--name", name,
            "--env", "POSTGRES_PASSWORD=restore-only", POSTGRES_IMAGE,
        ], cwd=project_dir)
        deadline = time.monotonic() + 60
        while True:
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], cwd=project_dir, capture_output=True)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise BackupError("Fresh PostgreSQL restore did not become ready")
            time.sleep(1)
        run(["docker", "exec", name, "createdb", "-U", "postgres", "restore"], cwd=project_dir)
        run(["docker", "cp", str(dump), name + ":/tmp/backup.dump"], cwd=project_dir)
        run([
            "docker", "exec", name, "pg_restore", "-U", "postgres", "-d", "restore",
            "--exit-on-error", "--no-owner", "--no-acl", "/tmp/backup.dump",
        ], cwd=project_dir, timeout=180)
        restored_count = int(run([
            "docker", "exec", name, "psql", "-U", "postgres", "-d", "restore", "-Atc",
            "SELECT count(*) FROM project_events",
        ], cwd=project_dir).stdout.strip())
        restored_hashes = run([
            "docker", "exec", name, "psql", "-U", "postgres", "-d", "restore", "-Atc",
            "SELECT id, payload_hash FROM project_events ORDER BY id",
        ], cwd=project_dir).stdout.decode("utf-8").strip()
        if restored_count != before_count or restored_hashes != before_hashes:
            raise BackupError("Restored canonical rows differ from source")
        after_count, after_hashes = source_snapshot(project_dir)
        if after_count != before_count or after_hashes != before_hashes:
            raise BackupError("Source database changed during restore acceptance")
        receipt.update({"restore_verified": True, "source_unchanged": True})
    finally:
        subprocess.run(["docker", "rm", "--force", name], cwd=project_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    os.chmod(receipt_path, 0o600)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an isolated demo backup/restore")
    parser.add_argument("--project-dir", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only:
        if not verify_manifest(args.verify_only):
            raise SystemExit("Backup manifest is invalid")
        print(json.dumps({"status": "confirmed", "manifest_verified": True}))
        return
    output = args.output_dir or args.project_dir / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(json.dumps(acceptance(args.project_dir.resolve(), output), indent=2))


if __name__ == "__main__":
    main()

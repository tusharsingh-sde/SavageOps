"""SavageOps CLI: run, doctor, simulate, and init."""

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

import uvicorn


def main():
    """Command line entrypoint for savageops."""
    # Ensure stdout/stderr can output Unicode on Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="savageops",
        description="SavageOps: Multi-Agent AI DevOps & SRE Incident Auto-Remediation",
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # 1. run command
    run_parser = subparsers.add_parser("run", help="Start the SavageOps API and Sentinel server")
    run_parser.add_argument("--host", default="127.0.0.1", help="Binding host address")
    run_parser.add_argument("--port", type=int, default=8000, help="Binding port number")

    # 2. doctor command
    subparsers.add_parser("doctor", help="Verify dependencies and environment configuration")

    # 3. simulate command
    sim_parser = subparsers.add_parser("simulate", help="Push synthetic error logs to Loki")
    sim_parser.add_argument("--service", default="dummy-app", help="Service name for logs")
    sim_parser.add_argument("--count", type=int, default=6, help="Number of error log lines")

    # 4. init command
    subparsers.add_parser(
        "init", help="Generate local templates directory with docker-compose and configs"
    )

    args = parser.parse_args()

    if args.cmd == "run":
        uvicorn.run("savageops.main:app", host=args.host, port=args.port)

    elif args.cmd == "doctor":
        from savageops.health import run_checks

        res = run_checks()
        print("\n=== SavageOps System Diagnostics ===")
        for name, status_val in res.items():
            icon = (
                "[OK]"
                if status_val in ("ok", "fixture_mode")
                else "[WAIT]"
                if status_val == "starting"
                else "[FAIL]"
            )
            msg = "" if status_val in ("ok", "fixture_mode") else f"({status_val})"
            print(f"{icon:7} {name:15} {msg}")

        all_ok = all(v in ("ok", "fixture_mode", "starting") for v in res.values())
        print("====================================\n")
        sys.exit(0 if all_ok else 1)

    elif args.cmd == "simulate":
        from savageops.simulate import push_fake_error

        try:
            push_fake_error(service=args.service, n=args.count)
            print("[OK] Fake errors successfully pushed to Loki. Awaiting alert trigger.")
        except Exception as e:
            print(f"[FAIL] Failed to push errors to Loki: {e}")
            sys.exit(1)

    elif args.cmd == "init":
        src = resources.files("savageops") / "templates"
        dest = Path.cwd() / "savageops-config"
        shutil.copytree(src, dest, dirs_exist_ok=True)
        packaged_env = dest / "env.example"
        if packaged_env.exists():
            packaged_env.replace(dest / ".env.example")
        print(f"[OK] savageops-config/ created at {dest.resolve()}")
        print("Included: .env.example, docker-compose.yml, and Grafana provisioning configs.")


if __name__ == "__main__":
    main()

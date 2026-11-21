"""SavageOps CLI: run, doctor, simulate, and init."""

import argparse
import os
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
    run_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1 for in-memory rate limits)",
    )

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

    # 5. models command
    models_parser = subparsers.add_parser(
        "models", help="List model IDs from configured LLM providers and verify chains"
    )
    models_parser.add_argument(
        "--all",
        action="store_true",
        help="Print full list of model IDs returned by each provider",
    )

    args = parser.parse_args()

    if args.cmd == "run":
        uvicorn.run("savageops.main:app", host=args.host, port=args.port, workers=args.workers)

    elif args.cmd == "doctor":
        from savageops.health import run_checks

        llm_details: dict = {}
        res = run_checks(ping_llms=True, llm_details=llm_details)
        print("\n=== SavageOps System Diagnostics ===")
        for name, status_val in res.items():
            icon = (
                "[OK]"
                if status_val in ("ok", "fixture_mode", "configured")
                else "[SKIP]"
                if status_val == "skipped"
                else "[WAIT]"
                if status_val == "starting"
                else "[FAIL]"
            )
            detail = llm_details.get(name)
            if detail:
                target = detail["target"]
                latency_ms = detail["latency_ms"]
                if status_val == "ok":
                    listed_note = (
                        "" if detail.get("listed") else " - OK (model not listed)"
                    )
                    msg = f"({target}, {latency_ms}ms{listed_note})"
                else:
                    msg = f"({target}, {latency_ms}ms, {status_val})"
            else:
                msg = "" if status_val in ("ok", "fixture_mode") else f"({status_val})"
            print(f"{icon:7} {name:15} {msg}")

        all_ok = all(
            v in ("ok", "fixture_mode", "starting", "skipped", "configured")
            for v in res.values()
        )
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

    elif args.cmd == "models":
        from savageops.llm import (
            PROVIDERS,
            check_chain_models,
            get_used_providers,
            list_provider_models,
        )

        print("\n=== Configured LLM Provider Models ===")
        used_providers = get_used_providers()
        models_by_provider = list_provider_models()
        show_all = bool(getattr(args, "all", False))
        has_flags = False
        for prov, models in models_by_provider.items():
            key_var = PROVIDERS[prov]["env_key"]
            has_key = bool(os.getenv(key_var, "").strip())
            if not has_key and prov not in used_providers:
                print(f"[{prov.upper()}] skipped ({key_var} not set)")
            elif not has_key and prov in used_providers:
                has_flags = True
                print(f"[{prov.upper()}] [FAIL] Missing required key {key_var}")
            elif not models:
                print(f"[{prov.upper()}] 0 models available (no models returned)")
            elif show_all:
                print(f"\n[{prov.upper()}] ({len(models)} models available):")
                for m_id in models:
                    print(f"  - {m_id}")
            else:
                print(f"[{prov.upper()}] {len(models)} models available")

        print("\n=== Model Chain Verifications ===")
        chain_checks = check_chain_models(available_models=models_by_provider)
        for chk in chain_checks:
            role = chk["role"]
            entry = chk["entry"]
            status = chk["status"]
            if "FLAG" in status:
                has_flags = True
                print(f"[FLAG] {role:13} -> {entry:42} : {status}")
            else:
                print(f"[OK]   {role:13} -> {entry:42} : {status}")
        print("======================================\n")
        sys.exit(1 if has_flags else 0)


if __name__ == "__main__":
    main()

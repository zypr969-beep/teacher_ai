from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STEPS = [
    ("Discover official HETC references", ["discover_hetc_refs.py"]),
    ("Build reference bank", ["build_reference_bank.py"]),
    ("Index local photos", ["index_photos.py"]),
    ("Match groups", ["match_groups_v2.py"]),
]


def run(label: str, script: str) -> None:
    print("\n" + "=" * 72)
    print(label)
    print("=" * 72)
    result = subprocess.run([sys.executable, script], check=False)
    if result.returncode != 0:
        raise SystemExit(f"Step failed: {script} (exit code {result.returncode})")


def main() -> None:
    missing = [script for _, (script,) in STEPS if not Path(script).exists()]
    if missing:
        raise SystemExit("Missing pipeline scripts: " + ", ".join(missing))

    for label, (script,) in STEPS:
        run(label, script)

    print("\nPipeline complete.")
    print("Next: review output/results_v2.csv and confirm matches in the Streamlit app before organizing files.")


if __name__ == "__main__":
    main()

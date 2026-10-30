"""Explicit NAS integration probe; writes only disposable test directories.

Run on the NAS with sudo after authorizing temporary filesystem/container tests:
python3 scripts/nas-hardlink-probe.py --media /mnt/tank/media --other /mnt/tank/other-dataset
Existing services are inspected but never changed. No image is downloaded.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--other", type=Path, required=True)
    parser.add_argument("--container", default="ix-sonarr-sonarr-1")
    args = parser.parse_args()
    directories = []
    try:
        for parent in [args.media, args.other]:
            code = (
                'import tempfile; print(tempfile.mkdtemp(prefix=".barnaclarr-probe-",dir='
                + repr(str(parent))
                + "))"
            )
            path = subprocess.check_output(
                ["sudo", "-u", "#568", "python3", "-c", code], text=True
            ).strip()
            directories.append(path)
        source, other = directories
        image = json.loads(subprocess.check_output(["docker", "inspect", args.container]))[0][
            "Image"
        ]
        base = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--user",
            "568:568",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--entrypoint",
            "/bin/sh",
        ]
        cases = [
            (
                "single_mount",
                ["-v", source + ":/test"],
                'mkdir /test/source /test/target && printf probe > /test/source/a && ln /test/source/a /test/target/b && test /test/source/a -ef /test/target/b && stat -c "%d %i %h" /test/source/a /test/target/b',
                True,
            ),
            (
                "separate_mounts",
                ["-v", source + "/source:/source", "-v", source + "/target:/target"],
                "ln /source/a /target/c",
                False,
            ),
            (
                "different_datasets",
                ["-v", source + "/source:/source", "-v", other + ":/target"],
                "ln /source/a /target/d",
                False,
            ),
        ]
        for name, mounts, command, expected_success in cases:
            result = subprocess.run(
                base + mounts + [image, "-c", command], capture_output=True, text=True
            )
            print(
                json.dumps(
                    {
                        "case": name,
                        "exit_code": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
            )
            if expected_success:
                assert result.returncode == 0, "Single mount hardlink failed"
            else:
                assert result.returncode != 0 and "Cross-device link" in result.stderr, (
                    "Expected EXDEV"
                )
    finally:
        for directory in directories:
            shutil.rmtree(directory)


if __name__ == "__main__":
    main()

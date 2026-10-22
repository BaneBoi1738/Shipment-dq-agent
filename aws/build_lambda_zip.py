"""Build lambda.zip for the S3 audit Lambda. Works on Windows, Mac and Linux.

    python aws/build_lambda_zip.py            # Python 3.12 (default)
    python aws/build_lambda_zip.py --python 3.13

pandas/numpy are NOT bundled: attach the AWS-managed "AWS SDK for pandas" layer
(matching Python version) to the function instead. Only pure/small deps are packaged.
"""
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "aws" / "build"
OUT = ROOT / "aws" / "lambda.zip"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="3.12", help="Lambda Python version (must match the layer)")
    ap.add_argument("--arch", default="x86_64", choices=["x86_64", "aarch64"])
    args = ap.parse_args()

    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    platform = "manylinux2014_x86_64" if args.arch == "x86_64" else "manylinux2014_aarch64"
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--quiet", "--target", str(BUILD),
        "--platform", platform, "--implementation", "cp", "--python-version", args.python,
        "--only-binary=:all:", "pyyaml", "requests",
    ], check=True)
    shutil.copytree(ROOT / "src" / "dq_agent", BUILD / "dq_agent",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(BUILD.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(BUILD))
    print(f"Built {OUT} ({OUT.stat().st_size / 1_000_000:.1f} MB) for Python {args.python} / {args.arch}")
    print("Handler: dq_agent.s3_handler.lambda_handler")


if __name__ == "__main__":
    main()

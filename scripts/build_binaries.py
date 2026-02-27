import os
import subprocess
import sys


def build_binary():
    print("Starting Nuitka build process for tensor-grep (tg)...")

    # Nuitka flags
    nuitka_args = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--output-filename=tg",
        "--assume-yes-for-downloads",
        "--follow-imports",
        # Include necessary data files for our specific libraries
        "--include-package=tensor_grep",
        "--include-package=rich",
        "--include-package=typer",
        "--plugin-enable=pylint-warnings",
        "src/tensor_grep/cli/main.py",
    ]

    # Run Nuitka
    print(f"Running command: {' '.join(nuitka_args)}")
    result = subprocess.run(nuitka_args)

    if result.returncode == 0:
        print("\nBuild successful! Binary generated.")

        # Check if the file exists
        if os.name == "nt":
            if os.path.exists("tg.exe"):
                print("Found: tg.exe")
        else:
            if os.path.exists("tg"):
                print("Found: tg")
            elif os.path.exists("tg.bin"):
                print("Found: tg.bin")
    else:
        print(f"\nBuild failed with exit code: {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    build_binary()

import os
import shutil

CORPUS_DIR = "benchmarks/corpus"


def generate_corpus():
    if os.path.exists(CORPUS_DIR):
        shutil.rmtree(CORPUS_DIR)
    os.makedirs(CORPUS_DIR)

    # 1. Standard text files with mixed casing
    with open(f"{CORPUS_DIR}/app.log", "w", encoding="utf-8") as f:
        f.write("2026-01-01 INFO Starting application\n")
        f.write("2026-01-01 ERROR Failed to connect to DB\n")
        f.write("2026-01-01 warning Connection retry\n")
        f.write("2026-01-01 ERROR Timeout during handshake\n")
        f.write("2026-01-01 error The system is down\n")
        f.write("Some random context\n" * 10)
        f.write("2026-01-01 INFO Shutting down...\n")

    # 2. Source code with boundaries and types
    with open(f"{CORPUS_DIR}/main.py", "w", encoding="utf-8") as f:
        f.write("import sys\n")
        f.write("def do_something(target_word):\n")
        f.write("    return target_word + ' suffix'\n")
        f.write("target_word_extended = 5\n")
        f.write("class TheTarget(object):\n")
        f.write("    pass\n")

    # 3. Hidden file
    with open(f"{CORPUS_DIR}/.secret_config", "w", encoding="utf-8") as f:
        f.write("PASSWORD=my_super_secret_password\n")
        f.write("ERROR_LEVEL=fatal\n")

    # 4. Another file type to test -t
    with open(f"{CORPUS_DIR}/index.js", "w", encoding="utf-8") as f:
        f.write("const ERROR = 'fatal';\n")
        f.write("console.error('An error occurred');\n")

    print(f"Test corpus generated at {CORPUS_DIR}")


if __name__ == "__main__":
    generate_corpus()

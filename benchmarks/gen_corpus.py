from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

AST_PARITY_CASES: list[dict[str, str]] = [
    {
        "id": "python_function_return",
        "language": "python",
        "relative_path": "python/01_function_return.py",
        "pattern": "def $F($$$ARGS): return $EXPR",
        "source": "def add(a, b): return a + b\n",
    },
    {
        "id": "python_class_pass",
        "language": "python",
        "relative_path": "python/02_class_pass.py",
        "pattern": "class $C: pass",
        "source": "class Greeter: pass\n",
    },
    {
        "id": "python_assignment",
        "language": "python",
        "relative_path": "python/03_assignment.py",
        "pattern": "$LEFT = $RIGHT",
        "source": "result = source\n",
    },
    {
        "id": "python_for_loop",
        "language": "python",
        "relative_path": "python/04_for_loop.py",
        "pattern": "for $ITEM in $ITER: print($ITEM)",
        "source": "for item in items: print(item)\n",
    },
    {
        "id": "python_if_return",
        "language": "python",
        "relative_path": "python/05_if_return.py",
        "pattern": "if $COND: return $VALUE",
        "source": "if ready: return value\n",
    },
    {
        "id": "python_while_break",
        "language": "python",
        "relative_path": "python/06_while_break.py",
        "pattern": "while $COND: break",
        "source": "while ready: break\n",
    },
    {
        "id": "python_with_open",
        "language": "python",
        "relative_path": "python/07_with_open.py",
        "pattern": "with open($PATH) as $HANDLE: $HANDLE.read()",
        "source": "with open(path) as handle: handle.read()\n",
    },
    {
        "id": "python_try_except",
        "language": "python",
        "relative_path": "python/08_try_except.py",
        "pattern": "try: $BODY except $ERR: $HANDLER",
        "source": "try: risky() except ValueError: recover()\n",
    },
    {
        "id": "python_list_comprehension",
        "language": "python",
        "relative_path": "python/09_list_comprehension.py",
        "pattern": "$OUT = [$ITEM for $ITEM in $ITER]",
        "source": "result = [item for item in items]\n",
    },
    {
        "id": "python_lambda_assignment",
        "language": "python",
        "relative_path": "python/10_lambda_assignment.py",
        "pattern": "$NAME = lambda $ARG: $BODY",
        "source": "handler = lambda value: value + 1\n",
    },
    {
        "id": "javascript_function_return",
        "language": "javascript",
        "relative_path": "javascript/01_function_return.js",
        "pattern": "function $F($$$ARGS) { return $EXPR; }",
        "source": "function add(a, b) { return a + b; }\n",
    },
    {
        "id": "javascript_arrow_function",
        "language": "javascript",
        "relative_path": "javascript/02_arrow_function.js",
        "pattern": "const $F = ($ARG) => $BODY;",
        "source": "const double = (value) => value * 2;\n",
    },
    {
        "id": "javascript_class_constructor",
        "language": "javascript",
        "relative_path": "javascript/03_class_constructor.js",
        "pattern": "class $C { constructor($$$ARGS) {} }",
        "source": "class Greeter { constructor(name) {} }\n",
    },
    {
        "id": "javascript_const_assignment",
        "language": "javascript",
        "relative_path": "javascript/04_const_assignment.js",
        "pattern": "const $LEFT = $RIGHT;",
        "source": "const result = source;\n",
    },
    {
        "id": "javascript_if_return",
        "language": "javascript",
        "relative_path": "javascript/05_if_return.js",
        "pattern": "if ($COND) { return $VALUE; }",
        "source": "if (ready) { return value; }\n",
    },
    {
        "id": "javascript_while_break",
        "language": "javascript",
        "relative_path": "javascript/06_while_break.js",
        "pattern": "while ($COND) { break; }",
        "source": "while (ready) { break; }\n",
    },
    {
        "id": "javascript_for_of",
        "language": "javascript",
        "relative_path": "javascript/07_for_of.js",
        "pattern": "for (const $ITEM of $ITER) { console.log($ITEM); }",
        "source": "for (const item of items) { console.log(item); }\n",
    },
    {
        "id": "javascript_try_catch",
        "language": "javascript",
        "relative_path": "javascript/08_try_catch.js",
        "pattern": "try { $BODY } catch ($ERR) { $HANDLER }",
        "source": "try { risky(); } catch (error) { recover(error); }\n",
    },
    {
        "id": "javascript_array_map",
        "language": "javascript",
        "relative_path": "javascript/09_array_map.js",
        "pattern": "const $OUT = $ITER.map(($ITEM) => $BODY);",
        "source": "const mapped = items.map((item) => item.id);\n",
    },
    {
        "id": "javascript_export_function",
        "language": "javascript",
        "relative_path": "javascript/10_export_function.js",
        "pattern": "export function $F($ARG) { return $ARG; }",
        "source": "export function greet(name) { return name; }\n",
    },
    {
        "id": "typescript_function_return",
        "language": "typescript",
        "relative_path": "typescript/01_function_return.ts",
        "pattern": "function $F($$$ARGS): $T { return $EXPR; }",
        "source": "function greet(name: string): string { return name; }\n",
    },
    {
        "id": "typescript_arrow_function",
        "language": "typescript",
        "relative_path": "typescript/02_arrow_function.ts",
        "pattern": "const $F = ($ARG: $T): $R => $BODY;",
        "source": "const double = (value: number): number => value * 2;\n",
    },
    {
        "id": "typescript_class_method",
        "language": "typescript",
        "relative_path": "typescript/03_class_method.ts",
        "pattern": "class $C { $M($ARG: $T): $R { return $ARG; } }",
        "source": "class Greeter { greet(name: string): string { return name; } }\n",
    },
    {
        "id": "typescript_interface",
        "language": "typescript",
        "relative_path": "typescript/04_interface.ts",
        "pattern": "interface $I { $FIELD: $TYPE }",
        "source": "interface Person { name: string }\n",
    },
    {
        "id": "typescript_const_assignment",
        "language": "typescript",
        "relative_path": "typescript/05_const_assignment.ts",
        "pattern": "const $LEFT: $TYPE = $RIGHT;",
        "source": "const result: string = source;\n",
    },
    {
        "id": "typescript_if_return_as",
        "language": "typescript",
        "relative_path": "typescript/06_if_return_as.ts",
        "pattern": "if ($COND) { return $VALUE as $TYPE; }",
        "source": "if (ready) { return value as string; }\n",
    },
    {
        "id": "typescript_for_of",
        "language": "typescript",
        "relative_path": "typescript/07_for_of.ts",
        "pattern": "for (const $ITEM of $ITER) { console.log($ITEM); }",
        "source": "for (const item of items) { console.log(item); }\n",
    },
    {
        "id": "typescript_try_catch",
        "language": "typescript",
        "relative_path": "typescript/08_try_catch.ts",
        "pattern": "try { $BODY } catch ($ERR) { $HANDLER }",
        "source": "try { risky(); } catch (error) { recover(error); }\n",
    },
    {
        "id": "typescript_union_type",
        "language": "typescript",
        "relative_path": "typescript/09_union_type.ts",
        "pattern": "type $NAME = $LEFT | $RIGHT;",
        "source": "type Result = Ok | Err;\n",
    },
    {
        "id": "typescript_export_function",
        "language": "typescript",
        "relative_path": "typescript/10_export_function.ts",
        "pattern": "export function $F($ARG: $T): $R { return $ARG; }",
        "source": "export function greet(name: string): string { return name; }\n",
    },
    {
        "id": "rust_function_println",
        "language": "rust",
        "relative_path": "rust/01_function_println.rs",
        "pattern": "fn $F() { println!($MSG); }",
        "source": 'fn main() { println!("hi"); }\n',
    },
    {
        "id": "rust_function_return",
        "language": "rust",
        "relative_path": "rust/02_function_return.rs",
        "pattern": "fn $F($ARG: $TYPE) -> $RET { $BODY }",
        "source": "fn add(value: i32) -> i32 { value + 1 }\n",
    },
    {
        "id": "rust_struct",
        "language": "rust",
        "relative_path": "rust/03_struct.rs",
        "pattern": "struct $S { $FIELD: $TYPE }",
        "source": "struct User { name: String }\n",
    },
    {
        "id": "rust_impl_method",
        "language": "rust",
        "relative_path": "rust/04_impl_method.rs",
        "pattern": "impl $S { fn $M(&self) { $BODY } }",
        "source": 'impl User { fn name(&self) { println!("user"); } }\n',
    },
    {
        "id": "rust_if_return",
        "language": "rust",
        "relative_path": "rust/05_if_return.rs",
        "pattern": "if $COND { return $VALUE; }",
        "source": "fn demo() { if ready { return value; } }\n",
    },
    {
        "id": "rust_while_break",
        "language": "rust",
        "relative_path": "rust/06_while_break.rs",
        "pattern": "while $COND { break; }",
        "source": "fn demo() { while ready { break; } }\n",
    },
    {
        "id": "rust_for_loop",
        "language": "rust",
        "relative_path": "rust/07_for_loop.rs",
        "pattern": "for $ITEM in $ITER { println!($FMT, $ITEM); }",
        "source": 'fn demo() { for item in items { println!("{}", item); } }\n',
    },
    {
        "id": "rust_let_assignment",
        "language": "rust",
        "relative_path": "rust/08_let_assignment.rs",
        "pattern": "let $LEFT = $RIGHT;",
        "source": "fn demo() { let result = compute(); }\n",
    },
    {
        "id": "rust_enum",
        "language": "rust",
        "relative_path": "rust/09_enum.rs",
        "pattern": "enum $E { $A, $B }",
        "source": "enum Result { Ok, Err }\n",
    },
    {
        "id": "rust_pub_function",
        "language": "rust",
        "relative_path": "rust/10_pub_function.rs",
        "pattern": "pub fn $F() { $BODY }",
        "source": "pub fn exported() { run(); }\n",
    },
]


def _recreate_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def write_manifest(output_dir: Path, manifest_path: Path) -> Path:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []

    for file_path in sorted(path for path in output_dir.rglob("*") if path.is_file()):
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        rows.append(f"{digest} *{file_path.relative_to(output_dir).as_posix()}")

    manifest_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest_path


def generate_python_ast_bench_corpus(
    output_dir: Path,
    *,
    file_count: int = 1000,
    total_loc: int = 50000,
    seed: int = 42,
) -> dict[str, object]:
    _recreate_dir(output_dir)
    rng = random.Random(seed)
    lines_per_file, extra_lines = divmod(total_loc, file_count)
    generated_loc = 0

    for file_index in range(file_count):
        current_lines = lines_per_file + (1 if file_index < extra_lines else 0)
        file_path = output_dir / f"module_{file_index:04d}.py"
        lines = []
        for line_index in range(current_lines):
            salt = rng.randint(1000, 9999)
            lines.append(
                f"def generated_{file_index}_{line_index}_{salt}(value_{line_index}): return value_{line_index} + {line_index}\n"
            )
        generated_loc += len(lines)
        file_path.write_text("".join(lines), encoding="utf-8")

    manifest_path = write_manifest(output_dir, output_dir.parent / f"{output_dir.name}.manifest.sha256")
    return {
        "corpus_dir": output_dir,
        "manifest_path": manifest_path,
        "file_count": file_count,
        "total_loc": generated_loc,
        "seed": seed,
    }


def generate_ast_parity_corpus(output_dir: Path) -> dict[str, object]:
    _recreate_dir(output_dir)
    for case in AST_PARITY_CASES:
        file_path = output_dir / case["relative_path"]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(case["source"], encoding="utf-8")

    manifest_path = write_manifest(output_dir, output_dir.parent / f"{output_dir.name}.manifest.sha256")
    return {
        "corpus_dir": output_dir,
        "manifest_path": manifest_path,
        "file_count": len(AST_PARITY_CASES),
        "languages": sorted({case["language"] for case in AST_PARITY_CASES}),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic benchmark corpora.")
    parser.add_argument(
        "--kind",
        choices=("ast-bench", "ast-parity"),
        required=True,
        help="Corpus flavor to generate.",
    )
    parser.add_argument("--out", required=True, help="Output directory for generated files.")
    parser.add_argument("--files", type=int, default=1000, help="File count for ast-bench corpus.")
    parser.add_argument("--loc", type=int, default=50000, help="Total LOC for ast-bench corpus.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed for ast-bench corpus.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.out).expanduser().resolve()

    if args.kind == "ast-bench":
        payload = generate_python_ast_bench_corpus(
            output_dir,
            file_count=args.files,
            total_loc=args.loc,
            seed=args.seed,
        )
    else:
        payload = generate_ast_parity_corpus(output_dir)

    serializable = {
        key: str(value) if isinstance(value, Path) else value for key, value in payload.items()
    }
    print(json.dumps(serializable, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from pathlib import Path
import tensor_grep.cli.diff_impact_refs as refs
import tensor_grep.cli.repo_map as rm
root = Path("_p")
root.mkdir(exist_ok=True)
(root/"app.py").write_text("def changed():\n    return 2\n")
(root/"test_app.py").write_text("import app, builtins\ng: object = builtins.getattr\nobserved = g(app, \"chan\" + \"ged\")\n")
payload = rm.build_repo_map(root.resolve())
print(refs._test_reference_evidence(payload, "changed", None, None, origin_files=(str((root/"app.py").resolve()),)))

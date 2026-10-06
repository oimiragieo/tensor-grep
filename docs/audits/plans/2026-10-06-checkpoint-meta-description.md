# Restore checkpoint action metadata

Labels head `b1b94475cb25a1f7a5949d70c679b784304066df` rewrote the checkpoint
meta-tool docstring and removed its three machine-consumed action bullets. The existing
`_annotate_legacy_tools` parser consequently advertises `action=?` for create, list and undo.
Full CI `37439164677` fails the unchanged legacy-description contract on both Linux Python
versions. The same assertion reproduces in the canonical Windows venv; iteration order can
name any of the three affected tools. This is a metadata regression, not a passing CI run.

Restore the three established `action="..."` / `(= tg_checkpoint_...)` bullets in the
`tg_checkpoint` docstring. The module is 5,700 lines against a 5,701-line cap: replace only
this docstring with concise prose and bullets within its existing line count. Preserve
the action mapping, opaque checkpoint ID for undo, server-confined path, trimmed printable
1–120-character create-only label, and explicit deletion of newer scoped files. Do not trim
unrelated comments, compress code or raise the size pin. Labels describe creates; IDs select undo.
Do not change the parser, dispatch, schemas, literal-label adapter, validation or error logic.
No new exception, size or capability allowance is authorized.

Run the unchanged legacy-description test on actual main as the positive base control and
on b1b as RED, then the full meta-dispatch, label/wire/sanitization and passthrough suites
after the fix. Verify the whole production module AST is identical after removing only this
docstring. Re-anchor inventory/handler line locations only if the unchanged guards require
it; preserve every call identity and classification. Run canonical quality/size/handler and
source replay checks with external deadlines, then exact-head independent review and fresh CI.
Retarget the pending mandatory Opus review to the new final artifact after its quota resets.
Actual publication and all final replay gates remain required.

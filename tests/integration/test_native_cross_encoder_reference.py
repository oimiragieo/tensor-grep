"""Optional real-asset score parity. No test ever downloads a model."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tensor_grep.core.cross_encoder_assets import default_asset_dir, verified_assets

_REFERENCE = """
import json,sys,numpy as np,onnxruntime as ort
from tokenizers import Tokenizer
model,tokenizer=sys.argv[1],sys.argv[2]
query,docs=json.load(sys.stdin)
tokenizer=Tokenizer.from_file(tokenizer)
tokenizer.no_padding()
tokenizer.enable_truncation(max_length=256)
options=ort.SessionOptions()
options.intra_op_num_threads=1
options.inter_op_num_threads=1
session=ort.InferenceSession(model, sess_options=options, providers=['CPUExecutionProvider'])
scores=[]
def prefix(text):
    return text.encode('utf-8')[:16384].decode('utf-8',errors='ignore')
for doc in docs:
    encoding=tokenizer.encode(prefix(query),prefix(doc))
    inputs={name:np.array([values],dtype=np.int64) for name,values in [
        ('input_ids',encoding.ids),('attention_mask',encoding.attention_mask),('token_type_ids',encoding.type_ids)]}
    scores.append(float(session.run(None,inputs)[0].reshape(-1)[0]))
print(json.dumps(scores))
"""


@pytest.mark.parametrize(
    ("query", "documents"),
    [
        ("retry failed requests", ["def retry_request(): return backoff()", "class Invoice: pass"]),
        ("Unicode 界 café", ["界" * 6000, "def café(): return 'café'"]),
    ],
)
def test_native_scores_match_reference(query: str, documents: list[str]) -> None:
    reference = os.environ.get("TG_CROSS_ENCODER_REFERENCE_PYTHON")
    if not reference or not default_asset_dir().is_dir():
        pytest.skip("requires explicitly installed assets and isolated reference interpreter")
    assert Path(reference).is_file()
    from tensor_grep import rust_core

    assert hasattr(rust_core, "cross_encoder_scores"), "rebuild the native extension"
    model, tokenizer, runtime = verified_assets(default_asset_dir())
    completed = subprocess.run(
        [
            reference,
            "-I",
            "-c",
            _REFERENCE,
            str(model),
            str(tokenizer),
        ],
        input=json.dumps([query, documents]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    expected = json.loads(completed.stdout)
    actual = rust_core.cross_encoder_scores(
        str(model), str(tokenizer), str(runtime), query, documents
    )
    assert actual == pytest.approx(expected, abs=1e-5, rel=1e-5)
    # Repeat through the reusable serialized native session.
    assert (
        rust_core.cross_encoder_scores(str(model), str(tokenizer), str(runtime), query, documents)
        == actual
    )

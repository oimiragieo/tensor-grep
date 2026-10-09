"""Pinned ONNX Runtime 1.24.4 CPU library wheels and extracted file hashes."""

from typing import Any

RUNTIMES: list[dict[str, Any]] = [
    {
        "wheel": "onnxruntime-1.24.4-cp311-cp311-macosx_14_0_arm64.whl",
        "url": "https://files.pythonhosted.org/packages/60/69/6c40720201012c6af9aa7d4ecdd620e521bd806dc6269d636fdd5c5aeebe/onnxruntime-1.24.4-cp311-cp311-macosx_14_0_arm64.whl",
        "sha256": "0bdfce8e9a6497cec584aab407b71bf697dac5e1b7b7974adc50bf7533bdb3a2",
        "size": 17332131,
        "libraries": {
            "libonnxruntime.1.24.4.dylib": [
                "f7a4418c1fbbb12705f26991c52cd9e4a90c54d4802c487d0facb20897790fe9",
                27708456,
            ]
        },
    },
    {
        "wheel": "onnxruntime-1.24.4-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl",
        "url": "https://files.pythonhosted.org/packages/38/e9/8c901c150ce0c368da38638f44152fb411059c0c7364b497c9e5c957321a/onnxruntime-1.24.4-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl",
        "sha256": "046ff290045a387676941a02a8ae5c3ebec6b4f551ae228711968c4a69d8f6b7",
        "size": 15152472,
        "libraries": {
            "libonnxruntime.so.1.24.4": [
                "f373ae5fd4d878ed17ebea26e5480bd3d0a15e3bd6bc94b878e6c1b73998d22a",
                18625376,
            ]
        },
    },
    {
        "wheel": "onnxruntime-1.24.4-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "url": "https://files.pythonhosted.org/packages/d5/b6/7a4df417cdd01e8f067a509e123ac8b31af450a719fa7ed81787dd6057ec/onnxruntime-1.24.4-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "sha256": "e54ad52e61d2d4618dcff8fa1480ac66b24ee2eab73331322db1049f11ccf330",
        "size": 17222993,
        "libraries": {
            "libonnxruntime.so.1.24.4": [
                "2d518e11c7767f9fdc4413bdbeb45acf30db504ff76a82fc19d9238a3f9f6c24",
                22159232,
            ]
        },
    },
    {
        "wheel": "onnxruntime-1.24.4-cp311-cp311-win_amd64.whl",
        "url": "https://files.pythonhosted.org/packages/dd/59/8febe015f391aa1757fa5ba82c759ea4b6c14ef970132efb5e316665ba61/onnxruntime-1.24.4-cp311-cp311-win_amd64.whl",
        "sha256": "b43b63eb24a2bc8fc77a09be67587a570967a412cccb837b6245ccb546691153",
        "size": 12594863,
        "libraries": {
            "onnxruntime.dll": [
                "ef720fc44a4ea48626bfe1ebd29642de20222d7f104a509ea305d9f3cb3b7850",
                16119840,
            ],
            "onnxruntime_providers_shared.dll": [
                "f123ce89224771bfeaf60a21cec31672985b731c312541da82bafaf38933f02c",
                22040,
            ],
        },
    },
    {
        "wheel": "onnxruntime-1.24.4-cp311-cp311-win_arm64.whl",
        "url": "https://files.pythonhosted.org/packages/32/84/4155fcd362e8873eb6ce305acfeeadacd9e0e59415adac474bea3d9281bb/onnxruntime-1.24.4-cp311-cp311-win_arm64.whl",
        "sha256": "e26478356dba25631fb3f20112e345f8e8bf62c499bb497e8a559f7d69cf7e7b",
        "size": 12259895,
        "libraries": {
            "onnxruntime.dll": [
                "a5842307bc58e562997053a59a8764ff0936b6738b5502a6103d084b1ee834fa",
                15476760,
            ],
            "onnxruntime_providers_shared.dll": [
                "fe06c0c7122f560e02022d1f122e271ed882d65de87b8a420cd9b38250bd3be2",
                21536,
            ],
        },
    },
]

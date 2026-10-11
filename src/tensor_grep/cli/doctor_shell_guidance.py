"""Shell quoting guidance included in the doctor payload."""

from typing import Any


def _doctor_shell_escaping_guidance() -> dict[str, Any]:
    return {
        "platform": "windows",
        "status": "informational",
        "powershell": {
            "summary": ("PowerShell double quotes expand $NAME before tensor-grep receives argv."),
            "recommendation": (
                "Use single quotes for literal patterns containing $, or escape `$` "
                "inside double-quoted PowerShell strings."
            ),
            "literal_pattern_example": "tg search '$NAME' .",
        },
        "cmd": {
            "summary": "cmd.exe parses metacharacters before tensor-grep receives argv.",
            "metacharacters": ["|", "&", "<", ">", "^", "(", ")"],
            "recommendation": (
                "Quote arguments or caret-escape cmd.exe metacharacters such as ^| and ^&; "
                "prefer normal interactive PowerShell `tg` over direct `tg.cmd` from "
                "PowerShell. MCP/stdio clients using Start-Process should target native "
                "`tg.exe` directly, not `tg.ps1`."
            ),
            "literal_pattern_example": 'cmd /c tg search "foo^|bar" .',
        },
    }

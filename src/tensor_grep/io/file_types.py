"""The built-in file types advertised by the Python search front end."""

TYPE_EXTENSIONS = {
    "asm": ("asm", "s", "S"),
    "c": ("c", "h"),
    "cpp": ("cc", "cpp", "cxx", "hpp", "hh", "hxx"),
    "csharp": ("cs",),
    "css": ("css",),
    "go": ("go",),
    "html": ("htm", "html"),
    "java": ("java",),
    "javascript": ("js", "jsx", "mjs", "cjs"),
    "json": ("json", "jsonl"),
    "kotlin": ("kt", "kts"),
    "lua": ("lua",),
    "markdown": ("md", "markdown"),
    "php": ("php",),
    "python": ("py", "pyi"),
    "rust": ("rs",),
    "swift": ("swift",),
    "toml": ("toml",),
    "typescript": ("ts", "tsx"),
    "yaml": ("yml", "yaml"),
}
TYPE_ALIASES = {"py": "python", "js": "javascript", "ts": "typescript"}
BUILTIN_TYPE_LIST = tuple(
    f"{name}: {', '.join(f'*.{extension}' for extension in extensions)}"
    for name, extensions in TYPE_EXTENSIONS.items()
)

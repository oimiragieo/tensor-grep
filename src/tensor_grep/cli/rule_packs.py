from __future__ import annotations

from typing import Any

_RULE_PACKS: dict[str, dict[str, Any]] = {
    "auth-safe": {
        "description": "Preview authentication hygiene checks for dynamic execution and hardcoded JWT secrets.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-auth-eval",
                    "pattern": "eval($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Do not evaluate untrusted code with eval() in authentication-sensitive paths.",
                },
                {
                    "id": "python-auth-exec",
                    "pattern": "exec($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Do not execute untrusted code with exec() in authentication-sensitive paths.",
                },
                {
                    "id": "python-jwt-encode-hardcoded-secret",
                    "pattern": 'jwt.encode($PAYLOAD, "$SECRET")',
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.encode().",
                },
                {
                    "id": "python-jwt-encode-hardcoded-secret-options",
                    "pattern": 'jwt.encode($PAYLOAD, "$SECRET", $$$ARGS)',
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.encode().",
                },
                {
                    "id": "python-jwt-decode-hardcoded-secret",
                    "pattern": 'jwt.decode($TOKEN, "$SECRET")',
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.decode().",
                },
                {
                    "id": "python-jwt-decode-hardcoded-secret-options",
                    "pattern": 'jwt.decode($TOKEN, "$SECRET", $$$ARGS)',
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.decode().",
                },
                {
                    "id": "python-hardcoded-jwt-secret",
                    "pattern": 'JWT_SECRET = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "python-hardcoded-secret-key",
                    "pattern": 'SECRET_KEY = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding authentication secret keys in source files.",
                },
                {
                    "id": "python-hardcoded-flask-jwt-secret",
                    "pattern": 'app.config["JWT_SECRET_KEY"] = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding Flask JWT secrets in source files.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-auth-eval",
                    "pattern": "eval($$$ARGS)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Do not evaluate untrusted code with eval() in authentication-sensitive paths.",
                },
                {
                    "id": "javascript-new-function",
                    "pattern": "new Function($$$ARGS)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Do not dynamically compile code with Function() in authentication-sensitive paths.",
                },
                {
                    "id": "javascript-jwt-sign-hardcoded-secret",
                    "pattern": 'jwt.sign($PAYLOAD, "$SECRET")',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.sign().",
                },
                {
                    "id": "javascript-jwt-sign-hardcoded-secret-options",
                    "pattern": 'jwt.sign($PAYLOAD, "$SECRET", $$$ARGS)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.sign().",
                },
                {
                    "id": "javascript-jwt-verify-hardcoded-secret",
                    "pattern": 'jwt.verify($TOKEN, "$SECRET")',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.verify().",
                },
                {
                    "id": "javascript-jwt-verify-hardcoded-secret-options",
                    "pattern": 'jwt.verify($TOKEN, "$SECRET", $$$ARGS)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.verify().",
                },
                {
                    "id": "javascript-hardcoded-jwt-secret",
                    "pattern": 'const JWT_SECRET = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "javascript-hardcoded-jwt-secret-camel",
                    "pattern": 'const jwtSecret = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "javascript-hardcoded-express-jwt-secret",
                    "pattern": 'app.set("jwtSecret", "$SECRET")',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding Express JWT secrets in source files.",
                },
            ],
            "typescript": [
                {
                    "id": "typescript-auth-eval",
                    "pattern": "eval($$$ARGS)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Do not evaluate untrusted code with eval() in authentication-sensitive paths.",
                },
                {
                    "id": "typescript-new-function",
                    "pattern": "new Function($$$ARGS)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Do not dynamically compile code with Function() in authentication-sensitive paths.",
                },
                {
                    "id": "typescript-jwt-sign-hardcoded-secret",
                    "pattern": 'jwt.sign($PAYLOAD, "$SECRET")',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.sign().",
                },
                {
                    "id": "typescript-jwt-sign-hardcoded-secret-options",
                    "pattern": 'jwt.sign($PAYLOAD, "$SECRET", $$$ARGS)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in jwt.sign().",
                },
                {
                    "id": "typescript-jwt-verify-hardcoded-secret",
                    "pattern": 'jwt.verify($TOKEN, "$SECRET")',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.verify().",
                },
                {
                    "id": "typescript-jwt-verify-hardcoded-secret-options",
                    "pattern": 'jwt.verify($TOKEN, "$SECRET", $$$ARGS)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in jwt.verify().",
                },
                {
                    "id": "typescript-hardcoded-jwt-secret",
                    "pattern": 'const JWT_SECRET = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "typescript-hardcoded-jwt-secret-camel",
                    "pattern": 'const jwtSecret = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "typescript-hardcoded-express-jwt-secret",
                    "pattern": 'app.set("jwtSecret", "$SECRET")',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding Express JWT secrets in source files.",
                },
            ],
            "rust": [
                {
                    "id": "rust-rhai-engine-eval",
                    "pattern": "rhai::Engine::new().eval($CODE)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Do not evaluate untrusted auth logic with rhai::Engine::eval().",
                },
                {
                    "id": "rust-rhai-engine-eval-with-scope",
                    "pattern": "engine.eval($CODE)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Do not evaluate untrusted auth logic with Engine::eval().",
                },
                {
                    "id": "rust-mlua-load-exec",
                    "pattern": "lua.load($CODE).exec()",
                    "language": "rust",
                    "severity": "high",
                    "message": "Do not execute untrusted auth logic with Lua::load().exec().",
                },
                {
                    "id": "rust-mlua-load-eval",
                    "pattern": "lua.load($CODE).eval()",
                    "language": "rust",
                    "severity": "high",
                    "message": "Do not evaluate untrusted auth logic with Lua::load().eval().",
                },
                {
                    "id": "rust-jsonwebtoken-encode-hardcoded-secret",
                    "pattern": 'EncodingKey::from_secret("$SECRET".as_bytes())',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT signing secrets in EncodingKey::from_secret().",
                },
                {
                    "id": "rust-jsonwebtoken-decode-hardcoded-secret",
                    "pattern": 'DecodingKey::from_secret("$SECRET".as_bytes())',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid hardcoding JWT verification secrets in DecodingKey::from_secret().",
                },
                {
                    "id": "rust-hardcoded-jwt-secret",
                    "pattern": 'let jwt_secret = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding JWT secrets in source files.",
                },
                {
                    "id": "rust-hardcoded-secret-key",
                    "pattern": 'let secret_key = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding authentication secret keys in source files.",
                },
            ],
        },
    },
    "crypto-safe": {
        "description": "Preview crypto hygiene checks for weak or obsolete hashing primitives.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-hashlib-md5",
                    "pattern": "hashlib.md5($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of hashlib.md5.",
                },
                {
                    "id": "python-hashlib-sha1",
                    "pattern": "hashlib.sha1($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of hashlib.sha1.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-createhash-md5",
                    "pattern": "crypto.createHash('md5')",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                },
                {
                    "id": "javascript-createhash-sha1",
                    "pattern": "crypto.createHash('sha1')",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of sha1.",
                },
            ],
            "typescript": [
                {
                    "id": "typescript-createhash-md5",
                    "pattern": "crypto.createHash('md5')",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                },
                {
                    "id": "typescript-createhash-sha1",
                    "pattern": "crypto.createHash('sha1')",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of sha1.",
                },
            ],
            "rust": [
                {
                    "id": "rust-md5-compute",
                    "pattern": "md5::compute($EXPR)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                }
            ],
        },
    },
    "secrets-basic": {
        "description": "Preview rules for obvious hardcoded secret assignments.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-hardcoded-password",
                    "pattern": 'password = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                },
                {
                    "id": "python-hardcoded-api-key",
                    "pattern": 'api_key = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding API key literals in source files.",
                },
                {
                    "id": "python-hardcoded-token",
                    "pattern": 'token = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding access token literals in source files.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-hardcoded-password",
                    "pattern": 'const password = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                },
                {
                    "id": "javascript-hardcoded-api-key",
                    "pattern": 'const apiKey = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding API key literals in source files.",
                },
                {
                    "id": "javascript-hardcoded-token",
                    "pattern": 'const token = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding access token literals in source files.",
                }
            ],
            "typescript": [
                {
                    "id": "typescript-hardcoded-password",
                    "pattern": 'const password = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                },
                {
                    "id": "typescript-hardcoded-api-key",
                    "pattern": 'const apiKey = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding API key literals in source files.",
                },
                {
                    "id": "typescript-hardcoded-token",
                    "pattern": 'const token = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding access token literals in source files.",
                }
            ],
            "rust": [
                {
                    "id": "rust-hardcoded-password",
                    "pattern": 'let password = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                },
                {
                    "id": "rust-hardcoded-api-key",
                    "pattern": 'let api_key = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding API key literals in source files.",
                },
                {
                    "id": "rust-hardcoded-token",
                    "pattern": 'let token = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding access token literals in source files.",
                }
            ],
        },
    },
    "deserialization-safe": {
        "description": "Preview deserialization safety checks for unsafe loaders and untrusted parsing.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-pickle-load",
                    "pattern": "pickle.load($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted data with pickle.load().",
                },
                {
                    "id": "python-pickle-loads",
                    "pattern": "pickle.loads($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted data with pickle.loads().",
                },
                {
                    "id": "python-dill-load",
                    "pattern": "dill.load($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted data with dill.load().",
                },
                {
                    "id": "python-dill-loads",
                    "pattern": "dill.loads($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted data with dill.loads().",
                },
                {
                    "id": "python-yaml-load",
                    "pattern": "yaml.load($DATA)",
                    "language": "python",
                    "severity": "high",
                    "message": "Use yaml.safe_load() or SafeLoader when parsing untrusted YAML.",
                },
                {
                    "id": "python-yaml-load-unsafe-loader",
                    "pattern": "yaml.load($DATA, Loader=yaml.Loader)",
                    "language": "python",
                    "severity": "high",
                    "message": "Use yaml.SafeLoader instead of yaml.Loader for untrusted YAML.",
                },
                {
                    "id": "python-yaml-load-full-loader",
                    "pattern": "yaml.load($DATA, Loader=yaml.FullLoader)",
                    "language": "python",
                    "severity": "high",
                    "message": "Use yaml.SafeLoader instead of yaml.FullLoader for untrusted YAML.",
                },
                {
                    "id": "python-pandas-read-pickle",
                    "pattern": "pandas.read_pickle($PATH)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid loading untrusted pickle data with pandas.read_pickle().",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-json-parse-untrusted",
                    "pattern": "JSON.parse($INPUT)",
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Review JSON.parse() on untrusted input before using the result in security-sensitive flows.",
                },
                {
                    "id": "javascript-json-parse-request-body",
                    "pattern": "JSON.parse(req.body)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid parsing request bodies directly with JSON.parse() in security-sensitive flows.",
                },
                {
                    "id": "javascript-json-parse-user-input",
                    "pattern": "JSON.parse(userInput)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid parsing untrusted user input directly with JSON.parse().",
                },
                {
                    "id": "javascript-object-assign-json-parse",
                    "pattern": "Object.assign({}, JSON.parse($INPUT))",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid merging JSON.parse() results from untrusted input into live objects.",
                },
                {
                    "id": "javascript-lodash-merge-json-parse",
                    "pattern": "_.merge({}, JSON.parse($INPUT))",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid merging JSON.parse() results from untrusted input into live objects.",
                },
                {
                    "id": "javascript-yaml-load",
                    "pattern": "yaml.load($DATA)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Use a safe YAML loader when parsing untrusted YAML.",
                },
            ],
            "typescript": [
                {
                    "id": "typescript-json-parse-untrusted",
                    "pattern": "JSON.parse($INPUT)",
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Review JSON.parse() on untrusted input before using the result in security-sensitive flows.",
                },
                {
                    "id": "typescript-json-parse-request-body",
                    "pattern": "JSON.parse(req.body)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid parsing request bodies directly with JSON.parse() in security-sensitive flows.",
                },
                {
                    "id": "typescript-json-parse-user-input",
                    "pattern": "JSON.parse(userInput)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid parsing untrusted user input directly with JSON.parse().",
                },
                {
                    "id": "typescript-object-assign-json-parse",
                    "pattern": "Object.assign({}, JSON.parse($INPUT))",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid merging JSON.parse() results from untrusted input into live objects.",
                },
                {
                    "id": "typescript-lodash-merge-json-parse",
                    "pattern": "_.merge({}, JSON.parse($INPUT))",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid merging JSON.parse() results from untrusted input into live objects.",
                },
                {
                    "id": "typescript-yaml-load",
                    "pattern": "yaml.load($DATA)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Use a safe YAML loader when parsing untrusted YAML.",
                },
            ],
            "rust": [
                {
                    "id": "rust-bincode-deserialize",
                    "pattern": "bincode::deserialize($BYTES)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted bytes with bincode::deserialize().",
                },
                {
                    "id": "rust-bincode-deserialize-from",
                    "pattern": "bincode::deserialize_from($READER)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid deserializing untrusted streams with bincode::deserialize_from().",
                },
                {
                    "id": "rust-serde-json-from-str",
                    "pattern": "serde_json::from_str($INPUT)",
                    "language": "rust",
                    "severity": "medium",
                    "message": "Review serde_json::from_str() on untrusted input before binding it to trusted types.",
                },
                {
                    "id": "rust-serde-json-from-slice",
                    "pattern": "serde_json::from_slice($BYTES)",
                    "language": "rust",
                    "severity": "medium",
                    "message": "Review serde_json::from_slice() on untrusted input before binding it to trusted types.",
                },
                {
                    "id": "rust-serde-yaml-from-str",
                    "pattern": "serde_yaml::from_str($INPUT)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Review serde_yaml::from_str() on untrusted input before binding it to trusted types.",
                },
                {
                    "id": "rust-ron-from-str",
                    "pattern": "ron::from_str($INPUT)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Review ron::from_str() on untrusted input before binding it to trusted types.",
                },
            ],
        },
    },
    "subprocess-safe": {
        "description": "Preview subprocess safety checks for shell invocation and command execution primitives.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-os-system",
                    "pattern": "os.system($CMD)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid executing shell commands with os.system().",
                },
                {
                    "id": "python-os-popen",
                    "pattern": "os.popen($CMD)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid executing shell commands with os.popen().",
                },
                {
                    "id": "python-subprocess-run-shell-true",
                    "pattern": "subprocess.run($CMD, shell=True)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid subprocess.run(..., shell=True) with untrusted commands.",
                },
                {
                    "id": "python-subprocess-call-shell-true",
                    "pattern": "subprocess.call($CMD, shell=True)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid subprocess.call(..., shell=True) with untrusted commands.",
                },
                {
                    "id": "python-subprocess-popen-shell-true",
                    "pattern": "subprocess.Popen($CMD, shell=True)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid subprocess.Popen(..., shell=True) with untrusted commands.",
                },
                {
                    "id": "python-subprocess-check-call-shell-true",
                    "pattern": "subprocess.check_call($CMD, shell=True)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid subprocess.check_call(..., shell=True) with untrusted commands.",
                },
                {
                    "id": "python-subprocess-check-output-shell-true",
                    "pattern": "subprocess.check_output($CMD, shell=True)",
                    "language": "python",
                    "severity": "high",
                    "message": "Avoid subprocess.check_output(..., shell=True) with untrusted commands.",
                },
                {
                    "id": "python-subprocess-getoutput",
                    "pattern": "subprocess.getoutput($CMD)",
                    "language": "python",
                    "severity": "medium",
                    "message": "Review subprocess.getoutput() usage for shell injection risks.",
                },
                {
                    "id": "python-subprocess-getstatusoutput",
                    "pattern": "subprocess.getstatusoutput($CMD)",
                    "language": "python",
                    "severity": "medium",
                    "message": "Review subprocess.getstatusoutput() usage for shell injection risks.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-child-process-exec",
                    "pattern": "child_process.exec($CMD)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid child_process.exec() with untrusted commands.",
                },
                {
                    "id": "javascript-child-process-exec-sync",
                    "pattern": "child_process.execSync($CMD)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid child_process.execSync() with untrusted commands.",
                },
                {
                    "id": "javascript-exec",
                    "pattern": "exec($CMD)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid exec() with untrusted commands.",
                },
                {
                    "id": "javascript-exec-sync",
                    "pattern": "execSync($CMD)",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid execSync() with untrusted commands.",
                },
                {
                    "id": "javascript-require-child-process-exec",
                    "pattern": 'require("child_process").exec($CMD)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid require(\"child_process\").exec() with untrusted commands.",
                },
                {
                    "id": "javascript-require-child-process-exec-sync",
                    "pattern": 'require("child_process").execSync($CMD)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid require(\"child_process\").execSync() with untrusted commands.",
                },
                {
                    "id": "javascript-spawn-sh",
                    "pattern": 'spawn("sh", $$$ARGS)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with spawn(\"sh\", ...).",
                },
                {
                    "id": "javascript-spawn-bash",
                    "pattern": 'spawn("bash", $$$ARGS)',
                    "language": "javascript",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with spawn(\"bash\", ...).",
                },
            ],
            "typescript": [
                {
                    "id": "typescript-child-process-exec",
                    "pattern": "child_process.exec($CMD)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid child_process.exec() with untrusted commands.",
                },
                {
                    "id": "typescript-child-process-exec-sync",
                    "pattern": "child_process.execSync($CMD)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid child_process.execSync() with untrusted commands.",
                },
                {
                    "id": "typescript-exec",
                    "pattern": "exec($CMD)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid exec() with untrusted commands.",
                },
                {
                    "id": "typescript-exec-sync",
                    "pattern": "execSync($CMD)",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid execSync() with untrusted commands.",
                },
                {
                    "id": "typescript-require-child-process-exec",
                    "pattern": 'require("child_process").exec($CMD)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid require(\"child_process\").exec() with untrusted commands.",
                },
                {
                    "id": "typescript-require-child-process-exec-sync",
                    "pattern": 'require("child_process").execSync($CMD)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid require(\"child_process\").execSync() with untrusted commands.",
                },
                {
                    "id": "typescript-spawn-sh",
                    "pattern": 'spawn("sh", $$$ARGS)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with spawn(\"sh\", ...).",
                },
                {
                    "id": "typescript-spawn-bash",
                    "pattern": 'spawn("bash", $$$ARGS)',
                    "language": "typescript",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with spawn(\"bash\", ...).",
                },
            ],
            "rust": [
                {
                    "id": "rust-command-new-sh",
                    "pattern": 'Command::new("sh")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with Command::new(\"sh\").",
                },
                {
                    "id": "rust-command-new-bash",
                    "pattern": 'Command::new("bash")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with Command::new(\"bash\").",
                },
                {
                    "id": "rust-command-new-cmd",
                    "pattern": 'Command::new("cmd")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with Command::new(\"cmd\").",
                },
                {
                    "id": "rust-command-new-powershell",
                    "pattern": 'Command::new("powershell")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with Command::new(\"powershell\").",
                },
                {
                    "id": "rust-std-command-new-sh",
                    "pattern": 'std::process::Command::new("sh")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with std::process::Command::new(\"sh\").",
                },
                {
                    "id": "rust-std-command-new-bash",
                    "pattern": 'std::process::Command::new("bash")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with std::process::Command::new(\"bash\").",
                },
                {
                    "id": "rust-std-command-new-cmd",
                    "pattern": 'std::process::Command::new("cmd")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with std::process::Command::new(\"cmd\").",
                },
                {
                    "id": "rust-std-command-new-powershell",
                    "pattern": 'std::process::Command::new("powershell")',
                    "language": "rust",
                    "severity": "high",
                    "message": "Avoid spawning a shell directly with std::process::Command::new(\"powershell\").",
                },
            ],
        },
    },
    "tls-safe": {
        "description": "Preview TLS hygiene checks for certificate verification bypasses.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-unverified-ssl-context",
                    "pattern": "ssl._create_unverified_context()",
                    "language": "python",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with ssl._create_unverified_context().",
                },
                {
                    "id": "python-requests-verify-false",
                    "pattern": "requests.get($URL, verify=False)",
                    "language": "python",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with verify=False.",
                },
                {
                    "id": "python-requests-post-verify-false",
                    "pattern": "requests.post($URL, verify=False)",
                    "language": "python",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with verify=False.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-reject-unauthorized-false",
                    "pattern": "rejectUnauthorized: false",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with rejectUnauthorized: false.",
                }
            ],
            "typescript": [
                {
                    "id": "typescript-reject-unauthorized-false",
                    "pattern": "rejectUnauthorized: false",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with rejectUnauthorized: false.",
                }
            ],
            "rust": [
                {
                    "id": "rust-danger-accept-invalid-certs",
                    "pattern": "danger_accept_invalid_certs(true)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Do not disable TLS certificate verification with danger_accept_invalid_certs(true).",
                }
            ],
        },
    },
}


def list_rule_packs() -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for name, spec in sorted(_RULE_PACKS.items()):
        languages = sorted(spec["languages"].keys())
        rules = sum(len(entries) for entries in spec["languages"].values())
        packs.append(
            {
                "name": name,
                "description": spec["description"],
                "category": spec["category"],
                "status": spec["status"],
                "default_language": spec["default_language"],
                "languages": languages,
                "rule_count": rules,
            }
        )
    return packs


def resolve_rule_pack(
    name: str, language: str | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    normalized_name = name.strip().lower()
    if normalized_name not in _RULE_PACKS:
        available = ", ".join(pack["name"] for pack in list_rule_packs())
        raise ValueError(f"Unknown built-in ruleset '{name}'. Available rulesets: {available}.")

    spec = _RULE_PACKS[normalized_name]
    selected_language = (language or spec["default_language"]).strip().lower()
    raw_rules = spec["languages"].get(selected_language)
    if not raw_rules:
        supported = ", ".join(sorted(spec["languages"].keys()))
        raise ValueError(
            f"Ruleset '{normalized_name}' does not support language '{selected_language}'. "
            f"Supported languages: {supported}."
        )

    rules = [
        {
            "id": str(rule["id"]),
            "pattern": str(rule["pattern"]),
            "language": str(rule["language"]),
            "severity": str(rule["severity"]),
            "message": str(rule["message"]),
        }
        for rule in raw_rules
    ]
    metadata = {
        "name": normalized_name,
        "description": spec["description"],
        "category": spec["category"],
        "status": spec["status"],
        "language": selected_language,
        "rule_count": len(rules),
    }
    return metadata, rules

"""Interactive-free adapter scaffold used by ``python app/main.py scaffold-adapter``."""
from __future__ import annotations

import re
from pathlib import Path


def _ident(value):
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    if not value or value[0].isdigit():
        value = "cli_" + value
    return value


def generate(root, *, name, executable, invocation="argv", output_format="text"):
    ident = _ident(name)
    if not executable:
        raise ValueError("executable 不能为空")
    base = Path(root) / "app" / "core" / "adapters"
    tests = Path(root) / "app" / "tests"
    base.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    adapter = base / (ident + ".py")
    test = tests / ("test_adapter_" + ident + ".py")
    if adapter.exists() or test.exists():
        raise FileExistsError("适配器或测试已存在：%s" % ident)
    adapter.write_text('''"""Generated CodeBee CLI adapter: {name}."""\n\nNAME = {name!r}\nEXECUTABLE = {exe!r}\nINVOCATION = {inv!r}\nOUTPUT_FORMAT = {fmt!r}\n\ndef command(prompt):\n    """Return an argv list; fill in provider-specific flags before use."""\n    return [EXECUTABLE, str(prompt)]\n\ndef parse_output(text):\n    return {{"text": str(text or ""), "format": OUTPUT_FORMAT}}\n'''.format(name=name, exe=executable, inv=invocation, fmt=output_format), encoding="utf-8")
    test.write_text('''import unittest\n\nfrom core.adapters import {ident}\n\nclass AdapterContractTest(unittest.TestCase):\n    def test_metadata(self):\n        self.assertTrue({ident}.NAME)\n        self.assertTrue({ident}.EXECUTABLE)\n        self.assertIsInstance({ident}.command("hello"), list)\n\nif __name__ == "__main__":\n    unittest.main()\n'''.format(ident=ident), encoding="utf-8")
    init = base / "__init__.py"
    if not init.exists():
        init.write_text("\"\"\"CLI adapter modules.\"\"\"\n", encoding="utf-8")
    registry = base / "registry.py"
    if not registry.exists():
        registry.write_text("\"\"\"Generated adapter registration placeholders.\"\"\"\n\nADAPTERS = {}\n", encoding="utf-8")
    return {"adapter": str(adapter), "test": str(test), "registry": str(registry), "id": ident,
            "next": "填写 command() 参数和 parse_output()，然后在 CLI 注册表启用。"}

#!/usr/bin/env python3
"""Build a minimal OpenAI draft ZIP; never upload or publish it."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("plugin.json", "mcp.json", "assets/icon-512.png", "LICENSE")


def openai_mcp_bytes():
    """OpenAI's documented auth extension is outside the portable 1.0 schema."""
    data = json.loads((ROOT / "mcp.json").read_text())
    data["mcpServers"]["commerce-engine"]["extensions"] = {
        "com.openai": {"auth": {
            "type": "oauth", "client": {"mode": "cimd"}, "baseScopes": ["ce"]
        }}
    }
    return (json.dumps(data, indent=2) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("Write the archive outside the plugin checkout.")
    for name in FILES:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            parser.error(f"Missing regular package file: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name in FILES:
            if name == "mcp.json":
                archive.writestr(name, openai_mcp_bytes())
            else:
                archive.write(ROOT / name, name)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == list(FILES)
    print(f"Draft ZIP: {output}")
    for name in FILES:
        print(f"  {name}")
    print("Upload it in OpenAI's plugin portal; nothing was uploaded.")


if __name__ == "__main__":
    main()

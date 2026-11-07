#!/usr/bin/env python3
"""Validate official schemas and local packaging checks, not portal readiness.

Requires jsonschema. No credentials, MCP calls, uploads or publication.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import urllib.request
from urllib.parse import urlparse
from zipfile import ZipFile

import jsonschema
from package import openai_mcp_bytes

ROOT = Path(__file__).resolve().parents[1]
NAME = "commerce-engine"
URL = "https://mcp.commercengine.io/mcp"
CATEGORIES = {
    "Business & Operations", "Communication", "Creativity", "Data & Analytics",
    "Developer Tools", "Education & Research", "Entertainment", "Finance",
    "Healthcare", "Other", "Productivity", "Scientific Research", "Security", "Travel",
}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read(root, name):
    return json.loads((root / name).read_text())


def schema_check(root, name, openai_archive=False):
    data = read(root, name)
    url = data["$schema"]
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read()
    schema = json.loads(payload)
    validator = jsonschema.validators.validator_for(schema)
    validator.check_schema(schema)
    instance = validator(schema, format_checker=jsonschema.FormatChecker())
    if openai_archive:
        errors = list(instance.iter_errors(data))
        if errors:
            print("KNOWN SCHEMA CONFLICT mcp.json: OpenAI server auth extensions are not allowed by Agent Plugins 1.0.0")
        # Check the portable fields independently; never call the full archive conformant.
        portable = json.loads(json.dumps(data))
        for server in portable["mcpServers"].values():
            server.pop("extensions", None)
        instance.validate(portable)
        print(f"PASS mcp.json portable fields only (OpenAI extension checked separately): {url}")
    else:
        instance.validate(data)
        print(f"PASS {name}: {url}")
    print(f"  schema sha256: {hashlib.sha256(payload).hexdigest()}")


def validate(root, archive=False):
    for name in ("plugin.json", "mcp.json"):
        schema_check(root, name, openai_archive=archive and name == "mcp.json")
    p = read(root, "plugin.json")
    check(p["name"] == NAME, "Plugin name differs")
    ext = p["extensions"]["com.openai"]
    ui = ext["interface"]
    for field, maximum in (("displayName", 30), ("shortDescription", 30),
                           ("longDescription", 4000), ("developerName", 80)):
        value = ui[field]
        check(isinstance(value, str) and 0 < len(value.strip()) <= maximum, field)
    check(ui["category"] in CATEGORIES, "Unsupported OpenAI category")
    for key in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
        value = ui[key]
        url = urlparse(value)
        check(url.scheme == "https" and url.netloc and not url.username
              and not url.password and len(value) <= 1024, key)
    prompts = ui["defaultPrompt"]
    check(isinstance(prompts, list) and 1 <= len(prompts) <= 3, "Default prompt count")
    check(all(isinstance(x, str) and 0 < len(x.strip()) <= 128 and "\n" not in x
              and "@" not in x for x in prompts), "Default prompt format")
    check(len({" ".join(x.split()) for x in prompts}) == len(prompts), "Duplicate prompts")
    for key in ("logo", "composerIcon"):
        relative = ui[key]
        asset = (root / relative).resolve()
        check(relative.startswith("./") and root.resolve() in asset.parents, "Icon containment")
        check(not (root / relative).is_symlink(), "Icon is a symlink")
        blob = asset.read_bytes()
        check(blob[:8] == b"\x89PNG\r\n\x1a\n", "Icon is not PNG")
        width, height = struct.unpack(">II", blob[16:24])
        check(width == height and 256 <= width <= 4096 and len(blob) <= 5 * 1024**2,
              "Icon size")
    print(f"PASS listing lengths: display={len(ui['displayName'])}, short={len(ui['shortDescription'])}, long={len(ui['longDescription'])}")
    print(f"PASS category, HTTPS URL syntax, prompts, icon: {width}x{height}, {len(blob)} bytes")
    mcp = read(root, "mcp.json")["mcpServers"]
    check(set(mcp) == {NAME}, "Expected one MCP server")
    server = mcp[NAME]
    check(server["url"] == URL and server["type"] == "streamable-http", "Portable endpoint")
    if archive:
        check(server.get("extensions") == {"com.openai": {"auth": {
            "type": "oauth", "client": {"mode": "cimd"}, "baseScopes": ["ce"]
        }}}, "OpenAI OAuth declaration")
    else:
        check("extensions" not in server, "Keep portable MCP free of OpenAI-only auth fields")
    check("headers" not in server, "Unexpected credential headers")
    review = ext["review"]
    cases = review["test_cases"]
    for kind, count in (("positive", 5), ("negative", 3)):
        check(len(cases[kind]) == count, f"Expected {count} {kind} cases")
        required = ["description", "prompt"]
        if kind == "positive":
            required += ["tools_triggered", "expected_behavior"]
        for case in cases[kind]:
            check(all(isinstance(case[k], str) and case[k].strip() for k in required),
                  f"Incomplete {kind} case")
            check(set(case) == set(required), "Unexpected case fields")
    check(0 < len(review["prompts"]) <= 20, "Review prompts count")
    check(all(isinstance(x, str) and 0 < len(x.strip()) <= 4000 for x in review["prompts"]), "Review prompts")
    check(ext["publication"]["release_notes"].strip(), "Release notes missing")
    check(not (root / ".app.json").exists() and "apps" not in ext and "apps" not in p,
          "Private app binding in public package")
    check(not (root / ".mcp.json").exists(), "Legacy .mcp.json shadows portable MCP")
    print("PASS Plugin Creator local checklist: connection config, 5 positive + 3 negative draft cases, release notes, no app bindings/headers")
    if not archive:
        schema_check(root, "server.json")
        for name in (".claude-plugin/plugin.json", ".cursor-plugin/plugin.json", "gemini-extension.json"):
            other = read(root, name)
            check(other["name"] == NAME and other["version"] == p["version"], f"Identity drift: {name}")
            check(other["description"] == p["description"], f"Description drift: {name}")
        claude = read(root, ".claude-plugin/plugin.json")
        check(claude["mcpServers"] == {NAME: {"type": "http", "url": URL}}, "Claude endpoint")
        check(claude["icon"] == ui["logo"], "Claude icon drift")
        for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"):
            check(claude[key].startswith("https://"), f"Claude URL: {key}")
        cursor = read(root, ".cursor-plugin/plugin.json")
        check(cursor["logo"] == ui["logo"].removeprefix("./"), "Cursor logo drift")
        gemini = read(root, "gemini-extension.json")
        check(gemini["mcpServers"] == {NAME: {"httpUrl": URL}}, "Gemini endpoint")
        registry = read(root, "server.json")
        check(registry["name"] == "io.github.commercengine/mcp" and registry["version"] == p["version"], "Registry identity")
        check(registry["remotes"] == [{"type": "streamable-http", "url": URL}], "Registry endpoint")
        cm = read(root, ".claude-plugin/marketplace.json")
        check(cm["name"] == NAME and cm["plugins"] == [{"name": NAME, "source": "./"}], "Claude marketplace")
        om = read(root, ".agents/plugins/marketplace.json")
        check(om["name"] == NAME and len(om["plugins"]) == 1, "Codex marketplace")
        entry = om["plugins"][0]
        check(entry["name"] == NAME and entry["source"] == {"source": "local", "path": "./"}, "Codex source")
        check(entry["category"] == ui["category"] and entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "Codex policy")
        print("PASS cross-client identities, versions, endpoints, logos and marketplace roots")
    gaps = []
    for section, field in ((review, "demo_recording_url"), (review, "commerce"),
                           (review, "commerce_description"), (ext["publication"], "countries")):
        if field not in section:
            gaps.append(field)
    print("OPEN submission fields: " + ", ".join(gaps))
    print("OPEN external checks: Sandbox case execution, reviewer access, domain/developer verification, scans and attestations")
    print("PASS local package checks; this is not an OpenAI portal validation or submission approval.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if not args.archive:
        validate(ROOT)
        return
    expected = {"plugin.json", "mcp.json", "assets/icon-512.png", "LICENSE"}
    with ZipFile(args.archive) as archive, tempfile.TemporaryDirectory() as directory:
        check(len(archive.namelist()) == len(expected) and set(archive.namelist()) == expected,
              "Unexpected archive inventory (marketplaces must be excluded)")
        check(archive.testzip() is None, "ZIP CRC failure")
        for info in archive.infolist():
            check((info.external_attr >> 16) & 0o170000 != 0o120000, "ZIP symlink")
        archive.extractall(directory)  # Names were checked against the exact allowlist.
        validate(Path(directory), archive=True)
        for name in expected:
            wanted = openai_mcp_bytes() if name == "mcp.json" else (ROOT / name).read_bytes()
            check(archive.read(name) == wanted, f"Stale archive: {name}")
        print("PASS final ZIP inventory and agreement with source plus documented OpenAI auth transform")


if __name__ == "__main__":
    main()

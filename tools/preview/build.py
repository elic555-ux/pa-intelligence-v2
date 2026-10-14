"""Build an isolated static preview from a chosen source checkout. Standard library only."""
import argparse
import hashlib
import json
from pathlib import Path
import re

FRONTEND = ("index.html", "deals.html", "report.html", "map.html", "secondary_sources.js")
POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' "
          "https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
          "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; "
          "connect-src 'self'; worker-src 'self' blob: https://cdnjs.cloudflare.com; "
          "object-src 'none'; base-uri 'self'; form-action 'self'")

def build(source, output, assets):
    source, output, assets = map(Path, (source, output, assets))
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    manifest["source_files_sha256"] = {}
    for name in FRONTEND:
        text = (source / name).read_text(encoding="utf-8")
        manifest["source_files_sha256"][name] = hashlib.sha256(text.encode()).hexdigest()
        if name.endswith(".html"):
            text = re.sub(r'<script\b[^>]*src="[^"]*@supabase/supabase-js[^"]*"[^>]*></script>\s*', "", text)
            text = re.sub(r"const SUPABASE_URL\s*=\s*'[^']*';", "const SUPABASE_URL = 'https://preview.invalid';", text)
            text = re.sub(r"const SUPABASE_KEY\s*=\s*'[^']*';", "const SUPABASE_KEY = 'preview-only';", text)
            text = re.sub(r"const PA_OWNER_ID\s*=\s*'[^']*';", "const PA_OWNER_ID = 'preview-owner';", text)
            head = '\n    <meta http-equiv="Content-Security-Policy" content="' + POLICY + '">\n    <script src="preview-runtime.js"></script>'
            text, count = re.subn(r'(<meta charset="UTF-8"\s*/?>)', lambda match: match[0] + head, text, count=1)
            if count != 1:
                raise ValueError("Missing charset marker: " + name)
            if "</body>" not in text:
                raise ValueError("Missing body marker: " + name)
            text = text.replace("</body>", '    <script src="preview-hooks.js"></script>\n</body>')
            text = text.replace('target="deals_tab"', 'target="pa_preview_deals_tab"')
            tag = manifest["source_sha"][:8]
            text = text.replace('src="preview-hooks.js"', 'src="preview-hooks.js?v=' + tag + '"')
            text = re.sub(r'src="secondary_sources\.js(?:\?[^"]*)?"',
                          'src="secondary_sources.js?v=' + tag + '"', text)
            if "@supabase/supabase-js" in text or ".supabase.co" in text:
                raise ValueError("Production client remains: " + name)
        (output / name).write_text(text, encoding="utf-8")
    for src, name in (("runtime.js", "preview-runtime.js"), ("hooks.js", "preview-hooks.js")):
        (output / name).write_text((assets / src).read_text(encoding="utf-8"), encoding="utf-8")
    fixtures = json.loads((assets / "fixtures.json").read_text(encoding="utf-8"))
    for name, value in fixtures.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid fixture path")
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "preview-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.output, Path(__file__).parent)
    print("Preview built from", result["source_sha"], "with", result["property_count"], "example properties")

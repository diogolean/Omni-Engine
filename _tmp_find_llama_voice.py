import json
import re

path = r"C:\Users\Freedom or Death\.cursor\projects\c-dev-omni-engine\agent-transcripts\d4f76a96-b628-4d2a-93a4-828958e44deb\d4f76a96-b628-4d2a-93a4-828958e44deb.jsonl"
pattern = re.compile(r".{0,120}llama.{0,160}", re.IGNORECASE)
with open(path, encoding="utf-8") as handle:
    for index, line in enumerate(handle, 1):
        if "llama" not in line.lower() or "neural" not in line.lower():
            continue
        obj = json.loads(line)
        content = obj.get("message", {}).get("content", [])
        if not content or not isinstance(content[0], dict):
            continue
        text = content[0].get("text") or ""
        if "user_query" not in text and obj.get("role") != "user":
            continue
        hits = pattern.findall(text)
        if not hits:
            continue
        print(f"LINE {index}")
        for hit in hits[:8]:
            if "neural" in hit.lower() or "voice" in hit.lower():
                print(hit.replace("\n", " "))
        print("---")

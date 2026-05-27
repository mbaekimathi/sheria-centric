"""One-off: split system_settings.html form sections into partials."""
from pathlib import Path
import re

text = Path("templates/system_settings.html").read_text(encoding="utf-8")
m = re.search(
    r'<form action.*?class="space-y-6">(.*)<div class="flex justify-end pb-8">',
    text,
    re.S,
)
body = m.group(1) if m else ""
parts = re.split(r"\n        <!-- (\d+\. [^>]+) -->\n", body)
slug_map = {
    "1": "company",
    "2": "contact",
    "3": "address",
    "4": "business-hours",
    "5": "social-media",
    "6": "legal",
    "7": "documents",
    "8": "billing",
    "9": "notifications",
    "11": "branding",
}
out = Path("templates/system_settings/sections")
out.mkdir(parents=True, exist_ok=True)
i = 1
while i < len(parts):
    label = parts[i]
    content = parts[i + 1].strip() if i + 1 < len(parts) else ""
    num = label.split(".", 1)[0].strip()
    slug = slug_map.get(num, num)
    (out / f"{slug}.html").write_text(content + "\n", encoding="utf-8")
    print(f"wrote {slug}.html ({len(content)} chars)")
    i += 2

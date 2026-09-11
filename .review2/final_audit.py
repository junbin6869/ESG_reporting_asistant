from pathlib import Path
from pypdf import PdfReader
import re

pdf = Path(r"C:\Users\junbi\Document\CS\Y3\Y3T3\FYP2\FYP_2304345.pdf")
reader = PdfReader(str(pdf))
patterns = [
    r"proposal", r"example", r"designer-assigned", r"subsequently manually validated",
    r"86\.67", r"86\.97", r"78\s*(?:/|out of)\s*90", r"80\.00",
    r"retrieval relevance", r"10\s*[-–]\s*15", r"hallucination", r"superior",
    r"appendix", r"to be updated", r"error!", r"reference source not found",
]

print(f"TOTAL_PDF_PAGES: {len(reader.pages)}")
all_pages=[]
for n, page in enumerate(reader.pages, 1):
    text = page.extract_text() or ""
    all_pages.append(text)
    hits=[pat for pat in patterns if re.search(pat, text, flags=re.I)]
    if hits:
        print(f"HITS page {n}: {', '.join(hits)}")

print("\n--- FIGURE/TABLE CAPTIONS IN BODY ---")
for n, text in enumerate(all_pages, 1):
    for line in text.splitlines():
        norm=' '.join(line.split())
        if re.match(r'^(Figure|Table)\s+[0-9]+(?:\.[0-9]+)+\s*:', norm, flags=re.I):
            print(f"p{n}: {norm}")

print("\n--- HEADINGS / KEY FRONT-MATTER ---")
for n, text in enumerate(all_pages, 1):
    for line in text.splitlines():
        norm=' '.join(line.split())
        if re.match(r'^(CHAPTER\s+[0-9]+|REFERENCES|LIST OF FIGURES|LIST OF TABLES|TABLE OF CONTENTS|LIST OF ABBREVIATIONS|APPENDIX|POSTER)$', norm, flags=re.I):
            print(f"p{n}: {norm}")

print("\n--- EXPECTED METRIC TOKENS ---")
for token in ["76 out of 90", "84.44%", "88.35%", "85.10%", "84.62%", "81.11%", "100%", "0.52"]:
    pages=[str(n) for n,t in enumerate(all_pages,1) if token.lower() in t.lower()]
    print(f"{token}: {', '.join(pages) if pages else 'NOT FOUND'}")

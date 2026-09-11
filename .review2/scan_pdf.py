from pypdf import PdfReader

PDF = r"C:\Users\junbi\Document\FYP\.review2\thesis-verify.pdf"
patterns = [
    "86.67", "86.97", "78/90", "80.00", "84.44", "84.62", "81.11", "0.52",
    "manually labelled", "designer-assigned", "Evaluation Against Project Aims",
    "Concluding Remark", "Retrieval Relevance", "10-15", "superior", "hallucination",
]

import re

reader = PdfReader(PDF)
caption = re.compile(r"^(Figure|Table)\s+(\d+(?:\.\d+)+)\s*:?\s*(.*)$", re.I)
print(f"PAGES {len(reader.pages)}", flush=True)
for physical_page, page in enumerate(reader.pages, 1):
    lines = [" ".join(line.split()) for line in (page.extract_text() or "").splitlines()]
    printed_page = None
    for index, line in enumerate(lines):
        if "Technology (Kampar Campus), UTAR" in line:
            printed_page = next(
                (candidate for candidate in lines[index + 1:index + 5]
                 if re.fullmatch(r"\d+", candidate)),
                None,
            )
            break
    for line in lines:
        match = caption.match(line)
        if match:
            print(f"physical={physical_page}; printed={printed_page}; {line}", flush=True)

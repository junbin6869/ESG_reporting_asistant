from pypdf import PdfReader
import re

PDF = r"C:\Users\junbi\Document\FYP\.review2\thesis-verify.pdf"
reader = PdfReader(PDF)

for physical, page in enumerate(reader.pages, 1):
    lines = [" ".join(line.split()) for line in (page.extract_text() or "").splitlines()]
    printed = None
    for index, line in enumerate(lines):
        if "Technology (Kampar Campus), UTAR" in line:
            printed = next((x for x in lines[index + 1:index + 5] if re.fullmatch(r"\d+", x)), None)
            break
    for line in lines:
        if re.match(r"^(?:[1-7]\.\d+(?:\.\d+)?\s+|CHAPTER [1-7] |REFERENCES$|POSTER$)", line):
            print(f"p={printed} physical={physical}: {line}")

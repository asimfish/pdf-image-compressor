from pathlib import Path

import fitz


def make_test_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content for testing.", fontsize=24)
    doc.save(path)
    doc.close()
    return path

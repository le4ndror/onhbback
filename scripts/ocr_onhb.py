#!/usr/bin/env python3
"""Produz cache de cabeçalhos OCR para as fases digitalizadas de 2012."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess

import pymupdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('edicao', type=Path)
    parser.add_argument('saida', type=Path)
    parser.add_argument('--trabalhadores', type=int, default=4)
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    def extract(job):
        path, i = job
        with pymupdf.open(path) as doc:
            page = doc[i]
            rect = pymupdf.Rect(0, 0, page.rect.width * .79, page.rect.height * .43)
            image = page.get_pixmap(dpi=155, clip=rect).tobytes('png')
        text = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'por', '--psm', '11'],
                              input=image, capture_output=True, check=True,
                              env={**os.environ, 'OMP_THREAD_LIMIT': '1'}).stdout
        (args.saida / f'{path.stem}-{i}.txt').write_bytes(text)

    tasks = []
    for path in sorted(args.edicao.glob('*fase*.pdf')):
        with pymupdf.open(path) as doc:
            tasks.extend((path, i) for i in range(len(doc)))
    with ThreadPoolExecutor(max_workers=args.trabalhadores) as pool:
        for _ in pool.map(extract, tasks):
            pass


if __name__ == '__main__':
    main()

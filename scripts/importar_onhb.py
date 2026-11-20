#!/usr/bin/env python3
"""Importa os PDFs locais em uma cópia do SQLite. Não acessa links externos."""
import argparse
from collections import defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
import subprocess
import unicodedata
from urllib.parse import urlsplit

import pymupdf as pdf

MODERN = re.compile(r'(\d+)([a-z]?)\s*/\s*(Questão|Tarefa|Instruções(?: \(.+\))?)', re.I)
EARLY = re.compile(r'(\d+)\s*[ªº°a]?\s*[-–—]?\s*(questão|tarefa)', re.I)
REF = re.compile(r'Documento\s+([a-z]?\d+[a-z]?)\b(?:\s+(.*?))?(?:\s+p\.\s*\d+)?$', re.I)
CHOICE = re.compile(r'(?:\(([A-D])\)|([A-D])[.)])\s*(.*)')
URL = re.compile(r'https?://[^\s<>“”"\[\]]+')


def norm(s):
    return ''.join(x for x in unicodedata.normalize('NFKD', s.casefold()) if not unicodedata.combining(x))


def key(s):
    return ' '.join(re.findall('[a-z0-9]+', norm(s)))


def doccode(s):
    return re.sub(r'(?<!\d)0+(?=\d)', '', s.lower())


def clean(s):
    s = s.replace('\x80', 'fi').replace('\x81', 'fl').replace('\u00ad', '-').replace('\u200b', '')
    return ' '.join(c for c in s.split()).strip()


def prose(rows, paragraphs=False):
    out = ''
    prev = None
    for row in rows:
        if isinstance(row, str):
            text = clean(row)
            gap = ' '
        else:
            text = row.text
            gap = '\n\n' if paragraphs and prev and row.page == prev.page and row.rect.y0 - prev.rect.y1 > 9 else ' '
            prev = row
        if not text:
            continue
        if out.endswith('-') and text[0].islower() and gap == ' ':
            out += text
        else:
            out += (gap if out else '') + text
    return out.strip()


@dataclass
class Line:
    page: int
    rect: pdf.Rect
    text: str
    size: float


class Pages:
    def __init__(self, doc):
        self.doc = doc
        self.lines = []
        for page in doc:
            rows = []
            for block in page.get_text('dict', flags=pdf.TEXTFLAGS_TEXT)['blocks']:
                for line in block['lines']:
                    text = clean(''.join(span['text'] for span in line['spans']))
                    rect = pdf.Rect(line['bbox']) * page.rotation_matrix
                    if text and rect.y0 > 10 and rect.y1 < page.rect.height - 11:
                        if not (text.isdigit() and rect.y0 > page.rect.height - 35):
                            rows.append(Line(page.number, rect, text, line['spans'][0]['size']))
            self.lines.append(sorted(rows, key=lambda row: (round(row.rect.y0, 1), row.rect.x0)))

    def links(self, page_num, clip=None, rows=()):
        page = self.doc[page_num]
        values = [x['uri'] for x in page.get_links() if x.get('uri') and (
            clip is None or (pdf.Rect(x['from']) * page.rotation_matrix).intersects(clip))]
        # Alguns PDFs antigos não têm anotações. Não tenta adivinhar URLs cortadas.
        if not values:
            for row in rows:
                if row.page == page_num:
                    values.extend(x.rstrip('.,;') for x in URL.findall(row.text) if '/' in x[8:])
        result = []
        for value in values:
            try:
                url = urlsplit(value)
                if url.scheme in ('http', 'https') and url.hostname and '.' in url.hostname and not re.search(r'\s', value):
                    result.append(value)
            except ValueError:
                pass
        return list(dict.fromkeys(result))

    def picture_boxes(self, page_num, clip):
        page = self.doc[page_num]
        seen = []
        for image in page.get_image_info():
            rect = (pdf.Rect(image['bbox']) * page.rotation_matrix) & page.rect
            if rect.width < 38 or rect.height < 28 or rect.is_empty or (rect & clip).get_area() < rect.get_area() * .96:
                continue
            if any((rect & other).get_area() >= min(rect.get_area(), other.get_area()) * .90 for other in seen):
                continue
            seen.append(rect)
        return seen

    def pics(self, page_num, clip):
        return [self.render(page_num, r) for r in self.picture_boxes(page_num, clip)]

    def render(self, page_num, rect):
        page = self.doc[page_num]
        zoom = min(2.5, 1400 / max(rect.width, rect.height))
        return page.get_pixmap(matrix=pdf.Matrix(zoom, zoom), clip=rect, alpha=False).tobytes('jpeg', jpg_quality=85)


@dataclass
class Source:
    code: str
    title: str
    kind: str
    page: int
    text: str = ''
    links: list = field(default_factory=list)
    images: list = field(default_factory=list)


@dataclass
class Question:
    num: int
    suffix: str
    kind: str
    page: int
    text: str = ''
    items: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def starts(rows, pattern, predicate=lambda _: True):
    return [(r, match) for r in rows if predicate(r) and (match := pattern.fullmatch(r.text))]


def portions(rows, rect_y0, rect_y1, left=0, right=10000):
    return [r for r in rows if rect_y0 <= r.rect.y0 < rect_y1 and left <= r.rect.x0 < right]


def alternatives(groups):
    items = defaultdict(list)
    letter = None
    for row in groups:
        if match := CHOICE.fullmatch(row.text):
            letter = match[1] or match[2]
            if len(items) >= 4 or letter != 'ABCD'[len(items)]:
                return {}
            items[letter].append(match[3])
        elif letter:
            items[letter].append(row.text)
    return {k: prose(v) for k, v in items.items()} if list(items) == list('ABCD') and all(v for v in items.values()) else {}


def add_links(q, links):
    known = {url for d in q.sources for url in d.links}
    for page, url in links:
        if url not in known:
            q.sources.append(Source('link-' + sha256(url.encode()).hexdigest()[:20], url, 'Link complementar', page, links=[url]))
            known.add(url)


def modern(pages):
    doc_header = re.compile(r'Documento\s+([a-z]?\d+[a-z]?)', re.I)
    marks, headers = [], []
    # A capa e o sumário também usam os números, mas não são enunciados.
    intro = next((i for i, rs in enumerate(pages.lines) if i >= 2 and rs and rs[0].text == 'Introdução' and not any('. . .' in r.text for r in rs)), 2)
    for i, rs in enumerate(pages.lines):
        if i <= intro:
            continue
        headers.extend(starts(rs, doc_header, lambda r: r.rect.x0 < 28 and r.size >= 9.5))
        if not headers:
            marks.extend(starts(rs, MODERN, lambda r: r.rect.x0 < 28 and r.size >= 9.5))
    def section(start, end):
        for i in range(start.page, (end.page + 1) if end else len(pages.lines)):
            p = pages.doc[i]
            low = start.rect.y1 if i == start.page else 0
            high = end.rect.y0 if end and i == end.page else p.rect.height - 22
            rows = portions(pages.lines[i], low, high)
            if rows or high > low + 12:
                yield i, rows, pdf.Rect(0, low, p.rect.width, high)

    docs = {}
    for idx, (heading, match) in enumerate(headers):
        next_heading = headers[idx+1][0] if idx+1 < len(headers) else None
        parts = list(section(heading, next_heading))
        flat = [row for _, rows, _ in parts for row in rows if row.text != 'Documentos']
        title = []
        for row in flat:
            if row.page != heading.page or row.size < heading.size - 0.2 or (title and row.rect.y0 - title[-1].rect.y1 > 5):
                break
            title.append(row)
        if not title and flat:
            title = flat[:1]
        body = prose(flat[len(title):], True)
        kind = re.search(r'TIPO DE DOCUMENTO:\s*(.*?)(?=\s+(?:ORIGEM|CRÉDITOS|TÉCNICA|DIMENSÕES|ANO)\s*:|$)', body, re.I)
        code = doccode(match[1]); links, images = [], []
        for i, rows, clip in parts:
            links.extend(pages.links(i, clip, rows)); images.extend(pages.pics(i, clip))
        docs[code] = Source('doc-' + code, prose(title) or 'Documento ' + match[1], clean(kind[1])[:100] if kind else 'Fonte histórica', heading.page, body, list(dict.fromkeys(links)), images)

    for idx, (heading, match) in enumerate(marks):
        kind = 'Objetiva' if match[3].lower() == 'questão' else 'Tarefa'
        q = Question(int(match[1]), match[2].lower(), kind, heading.page)
        end = marks[idx+1][0] if idx+1 < len(marks) else (headers[0][0] if headers else None)
        parts = list(section(heading, end))
        rows = [r for _, group, _ in parts for r in group if r.text not in ('Documentos', 'Questões')]
        lead, options, add = [], [], False
        skip_card = False
        for row in rows:
            if row.text == 'Conteúdo adicional':
                add = True
            if add:
                continue
            ref = REF.fullmatch(row.text)
            if ref and (row.rect.x0 > 30 or row.text.startswith('Documento ')):
                code = doccode(ref[1]); skip_card = True
                if code in docs and all(d.code != docs[code].code for d in q.sources):
                    q.sources.append(docs[code])
                elif code not in docs:
                    q.notes.append('referência sem ficha: ' + ref[1])
                continue
            if skip_card and row.rect.x0 >= 35 and not CHOICE.fullmatch(row.text):
                continue
            skip_card = False
            if kind == 'Objetiva' and (options or CHOICE.fullmatch(row.text)):
                options.append(row)
            else:
                lead.append(row)
        text = prose(lead, True)
        if kind == 'Objetiva':
            text = re.sub(r'^Esta questão possui quatro alternativas\..*?não será mais possível realizar alterações na questão\.\s*', '', text, flags=re.S | re.I)
        q.text = text
        q.items = alternatives(options)
        if kind == 'Objetiva' and not q.items:
            if not options and q.num in (47, 48):
                q.kind = 'Tarefa'
            else:
                q.notes.append('itens A–D não extraídos integralmente')
        for i, rs, clip in parts:
            # Imagens inseridas diretamente no enunciado (fora das fichas do apêndice).
            images = pages.pics(i, clip)
            if images:
                q.sources.append(Source(f'fig-{q.num}{q.suffix}-{i}', 'Imagens da questão', 'Imagem', i, images=images))
        add_links(q, [(i, url) for i, rs, clip in parts for url in pages.links(i, clip, rs)])
        yield q


def middle(pages, existing):
    groups = []
    docs = {}
    for i, rs in enumerate(pages.lines):
        page = pages.doc[i]; width = page.rect.width
        # As fichas trazem o código como primeira linha da coluna principal.
        first = [r for r in rs if r.rect.x0 < width * .58]
        if any(r.text == 'Sobre este documento' for r in rs) and len(first) >= 3 and re.fullmatch(r'\d+[a-z]?', first[0].text):
            head, title, kind = first[:3]
            code = doccode(head.text)
            side = next(r.rect.x0 for r in rs if r.text == 'Sobre este documento')
            clip = pdf.Rect(0, kind.rect.y1, side, page.rect.height - 22)
            # Preserva a ordem original de extração nas fontes com colunas internas.
            text = page.get_text(clip=clip).strip()
            notes = prose([r for r in rs if r.rect.x0 >= side - 1 and r.rect.y0 > title.rect.y0], True)
            images = pages.pics(i, clip)
            docs[code] = Source('doc-' + code, title.text, kind.text, i, '\n\n'.join(x for x in (text, notes) if x), pages.links(i, rows=rs), images)
            continue
        hs = starts(rs, MODERN, lambda r: r.rect.x0 < width * .32 and r.rect.y0 < page.rect.height * .52 and not r.text.endswith('QUESTÃO'))
        for h, m in hs[:1]:
            groups.append((h, m))
    for h, m in groups:
        i, n = h.page, int(m[1]); page = pages.doc[i]; rs = pages.lines[i]; width = page.rect.width
        q = Question(n, m[2].lower(), 'Objetiva' if m[3].lower() == 'questão' else 'Tarefa', i)
        marker = next((r for r in rs if r.text == 'Alternativas' and r.rect.x0 > h.rect.x0 + 40), None)
        side = next((r.rect.x0 for r in rs if r.text.startswith('Conteúdo adicional')), width)
        split = marker.rect.x0 - 2 if marker else width
        left = portions(rs, h.rect.y1, page.rect.height - 22, h.rect.x0 - 4, split)
        used, lead = set(), []
        for pos, row in enumerate(left):
            if row.text == 'Documento' or (ref := REF.fullmatch(row.text)):
                used.add(pos)
                if pos+1 < len(left) and left[pos+1].rect.x0 == row.rect.x0:
                    used.add(pos+1)
                if row.text != 'Documento' and (code := doccode(ref[1])) in docs and docs[code] not in q.sources:
                    q.sources.append(docs[code])
            if MODERN.fullmatch(row.text):
                used.add(pos)
        lead = [row for j, row in enumerate(left) if j not in used and row.rect.x0 < h.rect.x0 + 7]
        q.text = prose(lead, True)
        if marker:
            q.items = alternatives([r for r in portions(rs, marker.rect.y1, page.rect.height - 22, split, side) if not MODERN.fullmatch(r.text)])
        if q.kind == 'Objetiva' and not q.items:
            q.notes.append('itens A–D não extraídos integralmente')
        add_links(q, [(i, url) for url in pages.links(i, rows=rs)])
        if not q.sources:
            images = pages.pics(i, pdf.Rect(h.rect.x0, h.rect.y1, split, page.rect.height - 22))
            if images:
                q.sources.append(Source(f'fig-{n}', 'Imagens da questão', 'Imagem', i, images=images))
        yield q
    # Tarefas de avaliação por pares não seguem o modelo das questões objetivas.
    if not groups and len(existing) == 1:
        n, suffix = next(iter(existing))
        rows = [r for rs in pages.lines for r in rs]
        if any(re.fullmatch(rf'Tarefa\s+{n}', r.text, re.I) for r in rows):
            yield Question(n, suffix, 'Tarefa', 0, prose(rows, True))


def legacy_appendix(pages, known_names):
    marks = []
    for i, rows in enumerate(pages.lines):
        p = pages.doc[i]
        for r in rows:
            if re.fullmatch(r'Documentos da \d+[ªº] Fase', r.text, re.I):
                two = p.rotation in (90, 270)
                left = p.rect.width / 2 if two and r.rect.x0 >= p.rect.width / 2 else 0
                right = left + p.rect.width / 2 if two else p.rect.width
                candidates = [x for x in portions(rows, r.rect.y0 - 24, r.rect.y0 + 12, left, right)
                              if x.text != r.text and not x.text.startswith(('A prova deve', 'Este documento'))]
                if not two:
                    candidates = [x for x in candidates if x.rect.x0 <= r.rect.x0 + 4]
                else:
                    candidates = [x for x in candidates if x.rect.x0 < r.rect.x0 - 6]
                if len(candidates) >= 2:
                    marks.append((i, left, right, candidates[0], candidates[1], two))
    docs = {}
    for idx, (i, left, right, title, kind, two) in enumerate(marks):
        maxpage = i+1 if two else (marks[idx+1][0] if idx+1 < len(marks) else len(pages.lines))
        maxpage = max(maxpage, i+1)
        rs, pics, urls = [], [], []
        for j in range(i, maxpage):
            p = pages.doc[j]
            lower = kind.rect.y1 if i == j else 30
            upper = p.rect.height - 18
            rows = [x for x in portions(pages.lines[j], lower, upper, left, right)
                    if not re.match(r'^(?:Documentos da \d|Imagem no tamanho original|A prova deve|Este documento não serve)', x.text, re.I)]
            clip = pdf.Rect(left, lower, right, upper)
            rs.extend(rows)
            pics.extend(pages.pics(j, clip))
            urls.extend(pages.links(j, clip, rows))
        docs.setdefault(key(title.text), Source('appendix-' + sha256(key(title.text).encode()).hexdigest()[:20], title.text,
                                                 kind.text, i, prose(rs, True), list(dict.fromkeys(urls)), pics))
    # Nos primeiros modelos o título vem primeiro na ordem de leitura interna
    # do PDF. Só usa esta regra quando bate com um título do acervo da fase.
    for i, rows in enumerate(pages.lines):
        if not rows or any(EARLY.fullmatch(r.text) for r in rows):
            continue
        p = pages.doc[i]
        if p.rotation:
            continue
        raw = [clean(line) for line in p.get_text().splitlines() if clean(line)]
        if len(raw) < 2:
            continue
        code = key(raw[0])
        if code not in known_names or code in docs:
            continue
        heading = next((r for r in rows if key(r.text) == code), None)
        if heading is None:
            continue
        clip = pdf.Rect(0, heading.rect.y1, p.rect.width, p.rect.height - 18)
        # A ordem interna preserva textos que ocupam mais de uma coluna.
        body = [s for s in raw[2:] if not re.match(r'^(?:Este documento não serve|A prova deve|Documentos? da \d|Ver todos os documentos|\d+ de \d+$)', s)]
        docs[code] = Source('appendix-' + sha256(code.encode()).hexdigest()[:20], raw[0],
                            raw[1] if len(raw[1]) < 70 else 'Fonte histórica', i,
                            prose(body, True), pages.links(i, clip, rows), pages.pics(i, clip))
    return docs


def legacy(pages, existing, scan=None, document_names=None):
    known_names = {key(name) for names in (document_names or {}).values() for name in names}
    appendix = legacy_appendix(pages, known_names)
    fragments = []
    for i, rows in enumerate(pages.lines):
        p = pages.doc[i]; width = p.rect.width
        hs = starts(rows, EARLY, lambda r: r.rect.y0 > 35)
        if not hs and scan and (scan / f'{Path(pages.doc.name).stem}-{i}.txt').exists():
            ocr = (scan / f'{Path(pages.doc.name).stem}-{i}.txt').read_text()
            match = re.search(r'^\s*(\d+)\s*[º°ªa9]?\s*[-–—]?\s*(quest[aã]o|tarefa)\s*$', ocr, re.M | re.I)
            num = int(match[1]) if match else None
            # Cabeçalhos da edição de 2012 conferidos visualmente quando o OCR falhou.
            if Path(pages.doc.name).stem == '4_onhb_fase_2':
                num = {0: 11, 2: 13, 3: 14, 5: 16, 6: 17, 9: 20}.get(i, num)
            if num is None and re.search(r'^\s*Tarefa\s*$', ocr, re.M | re.I) and 'Questões' in ocr[:180]:
                tasks = [k[0] for k, old in existing.items() if old['tipo_nome'] == 'Tarefa']
                if len(tasks) == 1:
                    num = tasks[0]
            if (record := existing.get((num, ''))):
                q = Question(num, '', record['tipo_nome'], i)
                work = pdf.Rect(p.rect.width*.09, p.rect.height*.065, p.rect.width*.92, p.rect.height*.96)
                q.sources.append(Source(f'facsimile-{q.num}', 'Fac-símile da questão digitalizada', 'Fac-símile', i, images=[pages.render(i, work)]))
                q.notes.append('página identificada por OCR; texto anterior preservado')
                yield q
            continue
        for h, m in hs:
            left = h.rect.x0 - 2
            bound = width/2 if p.rotation and h.rect.x0 < width/2 else width
            side = min((r.rect.x0-2 for r in rows if r.text in ('Documentos relacionados', 'Conteúdos relacionados') and h.rect.x0 + 85 < r.rect.x0 < bound), default=bound - 25)
            end = min((r.rect.y0 for r, _ in hs if h.rect.y0 + 7 < r.rect.y0 and abs(r.rect.x0-h.rect.x0) < 12), default=p.rect.height-18)
            region = portions(rows, h.rect.y1, end, left, side)
            fragments.append((h, m, region, side, end))
    for h, m, rows, side, end in fragments:
        q = Question(int(m[1]), '', 'Tarefa' if m[2].lower() == 'tarefa' else 'Objetiva', h.page)
        marker = next((i for i, r in enumerate(rows) if r.text == 'Alternativas'), len(rows))
        if marker == len(rows):
            marker = next((i for i, r in enumerate(rows) if CHOICE.fullmatch(r.text)), len(rows))
        body, opts = rows[:marker], rows[marker:]
        q.items = alternatives(opts)
        if not q.items and (record := existing.get((q.num, ''))) and record['tipo_nome'] != 'Objetiva':
            q.kind = 'Tarefa'
        captions = [i for i, r in enumerate(body) if r.text in ('Documento', 'Documento relacionado')]
        stop_hints = re.compile(r'^(?:A partir|Com base|Baseando|Considerando|Leia|Observe|Analise|Escolha|Assinale|Sobre |A imagem|O documento|Podemos)', re.I)
        remove = set()
        for pos, j in enumerate(captions):
            next_j = captions[pos+1] if pos+1 < len(captions) else len(body)
            if j+1 >= next_j:
                continue
            title = body[j+1].text
            if stop_hints.match(title):
                continue
            stop = next((k for k in range(j+2, next_j) if stop_hints.match(body[k].text) and body[k].rect.y0 - body[k-1].rect.y1 >= 5), next_j)
            top = body[j].rect.y0; bottom = body[stop].rect.y0 if stop < len(body) else (opts[0].rect.y0 if opts else end)
            dclip = pdf.Rect(h.rect.x0 - 2, top, side, bottom)
            q.sources.append(appendix.get(key(title)) or Source(f'doc-{q.num}-{pos}', title, 'Fonte histórica', h.page, prose(body[j+2:stop], True), pages.links(h.page, dclip, body[j+2:stop]), pages.pics(h.page, dclip)))
            remove.update(range(j, stop))
        q.text = prose([r for j, r in enumerate(body) if j not in remove and r.rect.x0 < h.rect.x0 + 8 and r.text != 'Palavras-chave'], True)
        document_titles = (document_names or {}).get((q.num, ''), [])
        if not captions:
            boxes = pages.picture_boxes(h.page, pdf.Rect(h.rect.x0-2, h.rect.y1, side, opts[0].rect.y0 if opts else end))
            unlabelled = []
            for box in boxes:
                matches = [(abs(r.rect.y0-box.y0), name, r) for r in body for name in document_titles
                           if r.rect.x0 >= box.x1 + 5 and abs(r.rect.y0-box.y0) < 12 and key(r.text) == key(name)]
                if matches:
                    _, title, row = min(matches, key=lambda p: p[0])
                    label = next((r.text for r in body if abs(row.rect.x0-r.rect.x0) < 1 and 2 < r.rect.y0-row.rect.y1 < 12), 'Imagem')
                    q.sources.append(Source(f'fig-{q.num}-{len(q.sources)}', title, label, h.page, images=[pages.render(h.page, box)]))
                else:
                    unlabelled.append(pages.render(h.page, box))
            if unlabelled:
                q.sources.append(Source(f'fig-{q.num}', 'Fontes em imagem da questão', 'Imagem', h.page, images=unlabelled))
        page = pages.doc[h.page]
        bound = page.rect.width/2 if page.rotation and h.rect.x0 < page.rect.width/2 else page.rect.width
        clip = pdf.Rect(h.rect.x0-2, h.rect.y1, bound, end)
        within = portions(pages.lines[h.page], h.rect.y1, end, h.rect.x0-2, bound)
        for title in document_titles:
            if source := appendix.get(key(title)):
                inline = [d for d in q.sources if key(d.title) == key(title)]
                q.sources = [d for d in q.sources if key(d.title) != key(title)]
                q.sources.append(replace(source, images=source.images or [pic for d in inline for pic in d.images]))
        add_links(q, [(h.page, url) for url in pages.links(h.page, clip, within)])
        if not q.items and q.kind == 'Objetiva':
            q.notes.append('itens A–D não extraídos integralmente')
        yield q


def phase_files(folder):
    groups = defaultdict(list)
    for path in sorted(folder.glob('*.pdf')):
        name = norm(path.name)
        if not re.match(r'^(?:\d+\s*a?[ _-]+onhb|onhb\d+|fase)', name) or 'sobre a' in name:
            continue
        hit = re.search(r'fase\s*[_-]?\s*([0-6])\b', name.replace('_', ' ')) or re.search(r'\b([0-6])\s+fase\b', name.replace('_', ' '))
        if hit:
            phase = int(hit[1]); variant = ''
            if 'ensino fundamental' in name.replace('_', ' '):
                variant = 'Ensino Fundamental'
            elif 'ensino medio' in name.replace('_', ' '):
                variant = 'Ensino Médio'
            groups[(phase, variant)].append(path)
    return {k: min(v, key=lambda p: ('celular' not in p.name.lower(), p.name)) for k, v in groups.items()}


SCORES = re.compile(r'(?<!\S)(f?\d{1,3})[ \t]+([0-5])[ \t]+([0-5])[ \t]+([0-5])[ \t]+([0-5])(?=\s|$)', re.I)


def read_scores(folder, edition):
    scores, names, conflicts = {}, [], set()
    for file in sorted(folder.glob('*.pdf')):
        if 'gabarito' not in file.name.lower():
            continue
        pages = 2 if edition == 12 else 1
        text = subprocess.check_output(['pdftotext', '-layout', '-l', str(pages), file, '-'], text=True)
        matches = [m for line in text.splitlines() for m in SCORES.finditer(line)]
        if matches:
            names.append(file.name)
        for match in matches:
            label = match[1].lower()
            n = int(label.lstrip('f'))
            k = (0 if label.startswith('f') else None, n)
            values = tuple(map(int, match.groups()[1:]))
            if k in scores and scores[k] != values:
                conflicts.add(k)
            scores[k] = values
    return {k: v for k, v in scores.items() if k not in conflicts}, names, conflicts


def prepare(db):
    def add(table, column, spec):
        if column not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {spec}')
    add('SKYNET_questao', 'sufixo', "TEXT NOT NULL DEFAULT ''")
    add('SKYNET_questao', 'prova_pdf', 'TEXT')
    add('SKYNET_questao', 'prova_pagina', 'INTEGER')
    add('SKYNET_questao', 'gabarito_pdf', 'TEXT')
    add('SKYNET_documento', 'texto_fonte', 'TEXT')
    add('SKYNET_documento', 'arquivo_fonte', 'TEXT')
    add('SKYNET_documento', 'pagina_fonte', 'INTEGER')
    # Preserva a tabela original. A marca só distingue pontos desconhecidos dos zeros reais.
    db.executescript('''
    CREATE TABLE IF NOT EXISTS SKYNET_item_pontuacao_pendente (
        item_id INTEGER PRIMARY KEY REFERENCES SKYNET_item(id_item) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS SKYNET_documento_importacao (
        chave TEXT PRIMARY KEY, documento_id INTEGER NOT NULL REFERENCES SKYNET_documento(id_documento) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS SKYNET_documento_url (
        documento_id INTEGER NOT NULL REFERENCES SKYNET_documento(id_documento) ON DELETE CASCADE,
        url TEXT NOT NULL, PRIMARY KEY (documento_id, url)
    );
    CREATE TABLE IF NOT EXISTS SKYNET_imagem (
        hash TEXT PRIMARY KEY, conteudo BLOB NOT NULL
    );
    CREATE TABLE IF NOT EXISTS SKYNET_documento_imagem (
        documento_id INTEGER NOT NULL REFERENCES SKYNET_documento(id_documento) ON DELETE CASCADE,
        imagem_hash TEXT NOT NULL REFERENCES SKYNET_imagem(hash), PRIMARY KEY (documento_id, imagem_hash)
    );
    CREATE INDEX IF NOT EXISTS skynet_documento_imagem_hash ON SKYNET_documento_imagem(imagem_hash);
    ''')


def related(db, qid, source, filespec):
    path = filespec.as_posix()
    sourcekey = f'{path}::{source.code}'
    found = db.execute('SELECT documento_id FROM SKYNET_documento_importacao WHERE chave=?', (sourcekey,)).fetchone()
    if not found:
        matches = [r for r in db.execute('''SELECT d.* FROM SKYNET_documento d JOIN SKYNET_questao_documentos qd
            ON d.id_documento=qd.documento_id WHERE qd.questao_id=?''', (qid,))
            if key(r['nome_documento']) == key(source.title) and r['arquivo_fonte'] in (None, path)]
        if len(matches) == 1:
            docid = matches[0]['id_documento']
        else:
            docid = db.execute('INSERT INTO SKYNET_documento(nome_documento,tipo_documento) VALUES (?,?)', (source.title, source.kind)).lastrowid
        db.execute('INSERT INTO SKYNET_documento_importacao VALUES (?,?)', (sourcekey, docid))
    else:
        docid = found[0]
    db.execute('''UPDATE SKYNET_documento SET texto_fonte=CASE WHEN length(COALESCE(texto_fonte,''))>length(?) THEN texto_fonte ELSE ? END,
        arquivo_fonte=?,pagina_fonte=? WHERE id_documento=?''', (source.text, source.text, path, source.page+1, docid))
    db.execute('INSERT OR IGNORE INTO SKYNET_questao_documentos(questao_id,documento_id) VALUES (?,?)', (qid, docid))
    for url in source.links:
        db.execute('INSERT OR IGNORE INTO SKYNET_documento_url VALUES (?,?)', (docid, url))
    for image in source.images:
        ident = sha256(image).hexdigest()
        db.execute('INSERT OR IGNORE INTO SKYNET_imagem VALUES (?,?)', (ident, image))
        db.execute('INSERT OR IGNORE INTO SKYNET_documento_imagem VALUES (?,?)', (docid, ident))


def catalog_tags(db, qid, source):
    # Somente metadados explícitos, não datas ou palavras extraídas do enunciado.
    if not source.code.startswith('doc-') or 'Ficha técnica' not in source.text:
        return
    ficha = source.text.split('Ficha técnica', 1)[1]
    match = re.search(r'PALAVRAS[- ]CHAVE:\s*([^:\n]*?)(?=\s+(?:ORIGEM|CRÉDITOS|TÉCNICA|DATA|ANO|DIMENSÕES)\s*:|\n|$)', ficha, re.I)
    if match:
        for value in re.split(r'[,;]', match[1]):
            tag = value.strip(' .')
            if not 2 <= len(tag) <= 65 or '/' in tag or not any(ch.isalpha() for ch in tag):
                continue
            row = db.execute('SELECT id_tag FROM SKYNET_tag WHERE tag_nome = ? COLLATE NOCASE ORDER BY id_tag LIMIT 1', (tag,)).fetchone()
            tag_id = row[0] if row else db.execute('INSERT INTO SKYNET_tag(tag_nome) VALUES (?)', (tag,)).lastrowid
            db.execute('INSERT OR IGNORE INTO SKYNET_questao_tags(questao_id,tag_id) VALUES (?,?)', (qid, tag_id))
    for hit in re.finditer(r'(?:^|\s)(?:DATA|ANO):\s*(\d{4})(?=\s*(?:$|\n|(?:ORIGEM|CRÉDITOS|PALAVRAS|TÉCNICA|DIMENSÕES)\b))', ficha):
        year = int(hit[1])
        row = db.execute('SELECT id_ano FROM SKYNET_ano_historico WHERE ano = ? ORDER BY id_ano LIMIT 1', (year,)).fetchone()
        year_id = row[0] if row else db.execute('INSERT INTO SKYNET_ano_historico(ano) VALUES (?)', (year,)).lastrowid
        db.execute('INSERT OR IGNORE INTO SKYNET_questao_anos(questao_id,ano_historico_id) VALUES (?,?)', (qid, year_id))


def close_enough(official, previous):
    a, b = key(official), key(previous)
    if not a or not b:
        return False
    return (min(len(a), len(b)) > 15 and (a in b or b in a)) or SequenceMatcher(None, a[:2000], b[:2000], autojunk=False).ratio() >= .60


def import_phase(db, edition, year, phase, variant, filename, root, scores, names, scan):
    row = db.execute('SELECT id_edicao FROM SKYNET_edicao WHERE num_edicao=? AND ano_edicao=?', (edition, year)).fetchone()
    if row:
        edid = row[0]
    else:
        edid = db.execute('INSERT INTO SKYNET_edicao(num_edicao,ano_edicao) VALUES(?,?)', (edition, year)).lastrowid
    phase_rows = db.execute('SELECT id_fase, tipo_fase FROM SKYNET_fase WHERE id_edicao_id=? AND num_fase=?', (edid, phase)).fetchall()
    selected = [x for x in phase_rows if not variant or x[1] == variant]
    if len(selected) > 1:
        raise ValueError(f'fases duplicadas {edition}/{phase}/{variant}')
    if selected:
        fid = selected[0][0]
    else:
        kind = variant or {0: 'Introdutoria', 5: 'Desafio', 6: 'Finalista'}.get(phase, 'Avaliativa')
        fid = db.execute('INSERT INTO SKYNET_fase(num_fase,id_edicao_id,tipo_fase) VALUES(?,?,?)', (phase, edid, kind)).lastrowid
    existing = {(r['num_questao'], r['sufixo']): r for r in db.execute('''SELECT q.*,t.tipo_nome FROM SKYNET_questao q
        JOIN SKYNET_tipo_questao t ON t.id_tipo_questao=q.id_tipo_questao_id WHERE id_fase_id=?''', (fid,))}
    filespec = filename.relative_to(root)
    with pdf.open(filename) as doc:
        pages = Pages(doc)
        if edition >= 12:
            questions = list(modern(pages))
        elif edition >= 9:
            questions = list(middle(pages, existing))
        else:
            document_names = defaultdict(list)
            for row in db.execute('''SELECT q.num_questao,q.sufixo,d.nome_documento FROM SKYNET_questao q
                JOIN SKYNET_questao_documentos qd ON qd.questao_id=q.id_questao
                JOIN SKYNET_documento d ON d.id_documento=qd.documento_id WHERE q.id_fase_id=?''', (fid,)):
                document_names[(row[0], row[1])].append(row[2])
            questions = list(legacy(pages, existing, scan if edition == 4 else None, document_names))
    # Algumas fases imprimem a tarefa em partes a/b/c, mas o cadastro anterior
    # já tem um registro único. Atualiza esse registro sem perder seus vínculos.
    parts = defaultdict(list)
    for q in questions:
        if q.suffix and q.kind == 'Tarefa' and (q.num, '') in existing:
            parts[q.num].append(q)
    for n, group in parts.items():
        if any(q.num == n and not q.suffix for q in questions):
            continue
        questions = [q for q in questions if q not in group]
        group.sort(key=lambda q: q.suffix)
        sources = {source.code: source for q in group for source in q.sources}
        questions.append(Question(n, '', 'Tarefa', group[0].page,
                                  '\n\n'.join(f'Parte {q.suffix.upper()}\n{q.text}' for q in group), sources=list(sources.values())))
    duplicates = { (q.num, q.suffix) for q in questions if sum((p.num,p.suffix)==(q.num,q.suffix) for p in questions) > 1 }
    seen, notes = set(), []
    counts = dict(extraidas=0, novas=0, enunciados=0, itens_pdf=0, com_gabarito=0, fontes=0, imagens_vinculadas=0)
    for q in questions:
        k = (q.num, q.suffix); label = f'{q.num}{q.suffix}'
        if k in duplicates:
            notes.append(f'{label}: cabeçalho duplicado; não importado')
            continue
        old = existing.get(k)
        if not old and (not q.text or (q.kind == 'Objetiva' and not q.items)):
            notes.append(f'{label}: registro novo sem texto ou itens comprováveis')
            continue
        seen.add(k); counts['extraidas'] += 1
        for note in q.notes:
            notes.append(f'{label}: {note}')
        if old:
            qid = old['id_questao']
            last = old['enunciado']
            if q.text and (q.items or edition >= 12 or q.num in parts or close_enough(q.text, last)) and len(q.text) >= len(last)*.75:
                if q.text != last:
                    db.execute('UPDATE SKYNET_questao SET enunciado=? WHERE id_questao=?', (q.text, qid)); counts['enunciados'] += 1
            elif q.text and q.text != last:
                notes.append(f'{label}: enunciado anterior preservado (extração não correspondente)')
        else:
            kind_id = db.execute('SELECT id_tipo_questao FROM SKYNET_tipo_questao WHERE tipo_nome=?', (q.kind,)).fetchone()[0]
            qid = db.execute('INSERT INTO SKYNET_questao(num_questao,sufixo,enunciado,id_fase_id,id_tipo_questao_id) VALUES(?,?,?,?,?)', (q.num,q.suffix,q.text,fid,kind_id)).lastrowid
            counts['novas'] += 1; counts['enunciados'] += 1
        db.execute('UPDATE SKYNET_questao SET prova_pdf=?, prova_pagina=? WHERE id_questao=?', (filespec.as_posix(), q.page+1, qid))
        for source in q.sources:
            related(db, qid, source, filespec)
            catalog_tags(db, qid, source)
            counts['fontes'] += 1; counts['imagens_vinculadas'] += len(source.images)
        current = {x['identificador']: x for x in db.execute('SELECT * FROM SKYNET_item WHERE id_questao_id=?', (qid,))}
        values = scores.get((0 if phase==0 else None, q.num)) if phase <= 4 and not q.suffix else None
        if q.items:
            counts['itens_pdf'] += 1
        if old and old['tipo_nome'] != 'Objetiva' and not current and not q.items:
            values = None
        for index, letter in enumerate('ABCD'):
            if letter in current:
                item_id = current[letter]['id_item']
                if q.items:
                    db.execute('UPDATE SKYNET_item SET texto=? WHERE id_item=?', (q.items[letter], item_id))
                if values is not None:
                    db.execute('UPDATE SKYNET_item SET pontuacao=? WHERE id_item=?', (values[index], item_id))
                    db.execute('DELETE FROM SKYNET_item_pontuacao_pendente WHERE item_id=?', (item_id,))
            elif q.items:
                item_id = db.execute('INSERT INTO SKYNET_item(pontuacao,identificador,texto,id_questao_id) VALUES(?,?,?,?)', (values[index] if values is not None else 0,letter,q.items[letter],qid)).lastrowid
                if values is None:
                    db.execute('INSERT INTO SKYNET_item_pontuacao_pendente VALUES (?)', (item_id,))
        if (q.items or set(current) >= set('ABCD')) and values is not None:
            counts['com_gabarito'] += 1
            db.execute('UPDATE SKYNET_questao SET gabarito_pdf=? WHERE id_questao=?', (str(filespec.parent / names[0]), qid))
        elif q.items or current:
            notes.append(f'{label}: pontuação não verificada em gabarito local')
    # Mesmo quando o PDF da prova é ilegível, o gabarito oficial com numeração inequívoca pode confirmar os itens já cadastrados.
    for (num, suffix), old in sorted(existing.items()):
        if (num, suffix) in seen:
            continue
        notes.append(f'{num}{suffix}: enunciado/página sem extração confirmada')
        values = scores.get((0 if phase==0 else None, num)) if phase <= 4 and not suffix else None
        if old['tipo_nome'] == 'Objetiva' and values is not None:
            qid = old['id_questao']
            for letter, points in zip('ABCD', values):
                db.execute('UPDATE SKYNET_item SET pontuacao=? WHERE id_questao_id=? AND identificador=?', (points, qid, letter))
            if db.execute("SELECT COUNT(DISTINCT identificador) FROM SKYNET_item WHERE id_questao_id=? AND identificador IN ('A','B','C','D')", (qid,)).fetchone()[0] == 4:
                counts['com_gabarito'] += 1
                db.execute('UPDATE SKYNET_questao SET gabarito_pdf=? WHERE id_questao=?', (str(filespec.parent / names[0]), qid))
    return counts, list(dict.fromkeys(notes))


def rubrics(db, folder, edition, root):
    notes = []
    for file in sorted(folder.glob('*.pdf')):
        n = re.match(r'sistema de avaliacao da tarefa\s+(\d+)', norm(file.name))
        if not n:
            continue
        questions = db.execute('''SELECT id_questao FROM SKYNET_questao q JOIN SKYNET_fase f ON f.id_fase=q.id_fase_id
            JOIN SKYNET_edicao e ON e.id_edicao=f.id_edicao_id
            WHERE e.num_edicao=? AND q.num_questao=? AND q.sufixo=''
              AND NOT EXISTS (SELECT 1 FROM SKYNET_item i WHERE i.id_questao_id=q.id_questao)''', (edition, int(n[1]))).fetchall()
        if len(questions) != 1:
            notes.append(f'- {file.name}: tarefa não identificada de forma única.')
            continue
        with pdf.open(file) as doc:
            text = '\n\n'.join(p.get_text(sort=True).strip() for p in doc)
        if not text.strip():
            notes.append(f'- {file.name}: critérios sem texto selecionável.')
            continue
        related(db, questions[0][0], Source('criterios', file.stem, 'Critérios de avaliação', 0, text), file.relative_to(root))
        notes.append(f'- {file.name}: critérios vinculados à tarefa {n[1]}.')
    return notes


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('acervo', type=Path, help='pasta ONHB que contém as edições')
    cli.add_argument('banco', type=Path, help='SQLite existente (usar uma cópia)')
    cli.add_argument('--relatorio', type=Path, required=True)
    cli.add_argument('--edicao', type=int, action='append')
    cli.add_argument('--ocr-cache', type=Path, help='arquivos opcionais <stem do PDF>-<página a partir de zero>.txt')
    args = cli.parse_args()
    root = args.acervo.resolve()
    db = sqlite3.connect(args.banco.resolve().as_uri()+'?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    prepare(db)
    report = ['# Importação dos PDFs da ONHB\n',
              'As contagens são por fase. Vínculos de anos, períodos, tags e documentos anteriores foram preservados. As imagens foram extraídas dos PDFs; fac-símiles estão identificados separadamente. Notas não verificadas não foram presumidas.\n']
    summary = defaultdict(int)
    for folder in sorted(root.iterdir(), key=lambda p: int(re.match(r'\d+', p.name)[0]) if re.match(r'\d+', p.name) else 999):
        info = re.fullmatch(r'(\d+)a?_ONHB_(\d{4})', folder.name)
        if not info:
            continue
        edition, year = map(int, info.groups())
        if args.edicao and edition not in args.edicao:
            continue
        scores, names, conflicts = read_scores(folder, edition)
        report.append(f'\n## {edition}ª edição ({year})\n')
        report.append('Gabarito objetivo: ' + (', '.join(names) if names else 'não localizado na pasta da edição') + f'. {len(scores)} linhas lidas.\n')
        if conflicts:
            report.append(f'Numeração conflitante no gabarito: {conflicts}.\n')
        files = phase_files(folder)
        for (phase, variant), filename in sorted(files.items()):
            print(f'{edition:02}/{phase} {variant} {filename.name}', flush=True)
            with db:
                counts, notes = import_phase(db, edition, year, phase, variant, filename, root, scores, names, args.ocr_cache)
            report.append(f'\n### Fase {phase} {variant} — {filename.name}\n')
            report.append(', '.join(f'{k.replace("_", " ")}: {v}' for k,v in counts.items()) + '.\n')
            report.extend(f'- {note}' for note in notes)
            for k,v in counts.items():
                summary[k] += v
        missing = [row[0] for row in db.execute('SELECT f.num_fase FROM SKYNET_fase f JOIN SKYNET_edicao e ON e.id_edicao=f.id_edicao_id WHERE e.num_edicao=? ORDER BY 1', (edition,)) if row[0] not in {k[0] for k in files}]
        if missing:
            report.append('\nFases cadastradas sem PDF de prova selecionado: ' + ', '.join(map(str, missing)) + '. Registros anteriores preservados.\n')
        with db:
            notes = rubrics(db, folder, edition, root)
            if notes:
                report.append('\n### Critérios de avaliação\n')
                report.extend(notes)
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('o banco não passou na validação')
    db.close()
    args.relatorio.write_text('\n'.join([report[0], 'Totais: ' + ', '.join(f'{k.replace("_", " ")}: {v}' for k,v in summary.items()) + '.\n', *report[1:]])+'\n')
    print(dict(summary))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PROJECT_DIR, "web")
DATABASE = os.path.join(PROJECT_DIR, "dados", "db.sqlite3")
PAGE_SIZE = 100


def normalize_search(value):
    return "".join(
        char for char in unicodedata.normalize("NFD", value or "")
        if not unicodedata.category(char).startswith("M")
    ).lower()


def all_rows(connection, query, parameters=()):
    return [dict(row) for row in connection.execute(query, parameters)]


def load_questions(connection, query, parameters):
    rows = all_rows(connection, query, parameters)
    if not rows:
        return []

    ids = [row["id"] for row in rows]
    marks = ",".join("?" for _ in ids)
    relations = {
        "qd": ("documentos", "documento", "documento_id", "SKYNET_documento", "id_documento", "nome_documento", "tipo_documento"),
        "qp": ("periodos", "periodo", "periodo_id", "SKYNET_periodo", "id_periodo", "periodo_nome", "seculo"),
        "qt": ("tags", "tag", "tag_id", "SKYNET_tag", "id_tag", "tag_nome", None),
        "qa": ("anos", "ano_historico", "ano_historico_id", "SKYNET_ano_historico", "id_ano", "ano", None),
    }

    for key, (table, singular, foreign_key, related_table, related_id, first, second) in relations.items():
        join = f"""
            SELECT link.questao_id AS id_questao,
                   related.{related_id} AS id,
                   related.{first} AS first_value
                   {f", related.{second} AS second_value" if second else ""}
            FROM SKYNET_questao_{table} AS link
            JOIN {related_table} AS related ON related.{related_id} = link.{foreign_key}
            WHERE link.questao_id IN ({marks})
        """
        grouped = {}
        for relation in all_rows(connection, join, ids):
            item = {"id": relation["id"]}
            if key == "qd":
                item.update({"nome": relation["first_value"], "tipo": relation["second_value"]})
            elif key == "qp":
                item.update({"nome": relation["first_value"], "seculo": relation["second_value"]})
            elif key == "qt":
                item["nome"] = relation["first_value"]
            else:
                item["ano"] = relation["first_value"]
            grouped.setdefault(relation["id_questao"], []).append(item)
        for row in rows:
            row[key] = grouped.get(row["id"], [])

    items = all_rows(connection, f"""
        SELECT id_questao_id AS id_questao, id_item AS id, identificador,
               pontuacao, texto
        FROM SKYNET_item WHERE id_questao_id IN ({marks})
        ORDER BY identificador
    """, ids)
    grouped_items = {}
    for item in items:
        grouped_items.setdefault(item["id_questao"], []).append(item)
    for row in rows:
        row["itens"] = grouped_items.get(row["id"], [])
    return rows


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/questoes", "/api/filtros"):
            return super().do_GET()

        params = parse_qs(parsed.query)
        try:
            page = max(1, int(params.get("page", ["1"])[0]))
            page_size = min(1000, max(1, int(params.get("pageSize", [str(PAGE_SIZE)])[0])))
            with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                if parsed.path == "/api/filtros":
                    payload_data = {
                        "categorias": all_rows(connection, "SELECT id_tipo_questao AS id, tipo_nome AS nome FROM SKYNET_tipo_questao ORDER BY tipo_nome"),
                        "fases": all_rows(connection, "SELECT id_fase AS id, num_fase AS num, tipo_fase AS tipo, id_edicao_id AS id_edicao FROM SKYNET_fase ORDER BY num_fase"),
                        "edicoes": all_rows(connection, "SELECT id_edicao AS id, num_edicao AS num, ano_edicao AS ano FROM SKYNET_edicao ORDER BY ano_edicao DESC"),
                        "periodos": all_rows(connection, "SELECT id_periodo AS id, periodo_nome AS nome, seculo FROM SKYNET_periodo ORDER BY periodo_nome"),
                        "tags": all_rows(connection, "SELECT id_tag AS id, tag_nome AS nome FROM SKYNET_tag ORDER BY tag_nome"),
                    }
                else:
                    filters = []
                    values = []
                    query = params.get("q", [""])[0]
                    terms = list(dict.fromkeys(
                        part.strip() for part in re.split(r"\s+(?:e|ou)\s+|[,;]", query, flags=re.IGNORECASE)
                        if part.strip()
                    ))
                    if len(terms) > 20:
                        self.send_error(400, "Busque ate 20 temas de uma vez.")
                        return
                    category = params.get("categoria", [""])[0].strip()
                    phase = params.get("fase", [""])[0].strip()
                    period = params.get("periodo", [""])[0].strip()
                    edition = params.get("edicao", [""])[0].strip()
                    sort = params.get("sort", ["relevant"])[0]
                    tag_ids = [int(tag_id) for tag_id in params.get("tag", []) if tag_id.isdigit()]
                    if terms:
                        connection.create_function("normalizar", 1, normalize_search, deterministic=True)
                        theme_match = """(instr(normalizar(q.enunciado), ?) > 0
                            OR EXISTS (SELECT 1 FROM SKYNET_item i WHERE i.id_questao_id = q.id_questao AND instr(normalizar(i.texto), ?) > 0)
                            OR EXISTS (SELECT 1 FROM SKYNET_questao_documentos qd JOIN SKYNET_documento d ON d.id_documento = qd.documento_id WHERE qd.questao_id = q.id_questao AND instr(normalizar(d.nome_documento), ?) > 0)
                            OR EXISTS (SELECT 1 FROM SKYNET_questao_tags qt JOIN SKYNET_tag tg ON tg.id_tag = qt.tag_id WHERE qt.questao_id = q.id_questao AND instr(normalizar(tg.tag_nome), ?) > 0))"""
                        filters.append(" OR ".join([theme_match] * len(terms)))
                        for term in terms:
                            values.extend([normalize_search(term)] * 4)
                    if category:
                        filters.append("t.tipo_nome = ?")
                        values.append(category)
                    if phase:
                        filters.append("(f.tipo_fase = ? OR CAST(f.num_fase AS TEXT) = ?)")
                        values.extend([phase, phase])
                    if period:
                        filters.append("EXISTS (SELECT 1 FROM SKYNET_questao_periodos qp JOIN SKYNET_periodo p ON p.id_periodo = qp.periodo_id WHERE qp.questao_id = q.id_questao AND p.periodo_nome = ?)")
                        values.append(period)
                    if edition:
                        filters.append("e.ano_edicao = ?")
                        values.append(edition)
                    if tag_ids:
                        marks = ",".join("?" for _ in tag_ids)
                        filters.append(f"EXISTS (SELECT 1 FROM SKYNET_questao_tags qt WHERE qt.questao_id = q.id_questao AND qt.tag_id IN ({marks}))")
                        values.extend(tag_ids)
                    where = " WHERE " + " AND ".join("(" + clause + ")" for clause in filters) if filters else ""
                    base = """
                        FROM SKYNET_questao AS q
                        JOIN SKYNET_fase AS f ON f.id_fase = q.id_fase_id
                        JOIN SKYNET_edicao AS e ON e.id_edicao = f.id_edicao_id
                        JOIN SKYNET_tipo_questao AS t ON t.id_tipo_questao = q.id_tipo_questao_id
                    """
                    total = connection.execute("SELECT COUNT(*) " + base + where, values).fetchone()[0]
                    questions = load_questions(connection, """
                        SELECT q.id_questao AS id, q.num_questao AS num, q.enunciado,
                               q.id_fase_id AS id_fase, q.id_tipo_questao_id AS id_tipo,
                               f.num_fase AS fase_num, f.tipo_fase AS fase_tipo,
                               f.id_edicao_id AS id_edicao,
                               e.num_edicao AS edicao_num, e.ano_edicao AS edicao_ano,
                               t.tipo_nome AS tipo_nome
                    """ + base + where + " ORDER BY e.ano_edicao DESC, f.num_fase, q.num_questao LIMIT ? OFFSET ?", values + [page_size, (page - 1) * page_size])
                    payload_data = {"page": page, "pageSize": page_size, "total": total, "questions": questions}
            payload = json.dumps(payload_data, ensure_ascii=False)
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, sqlite3.Error) as error:
            body = json.dumps({"error": str(error)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    if not os.path.isfile(DATABASE):
        raise SystemExit(f"Banco SQLite não encontrado: {DATABASE}")
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print(f"Site e API disponíveis em http://127.0.0.1:8000 — banco: {DATABASE}")
    server.serve_forever()

'use strict';

const { createServer } = require('node:http');
const { readFile } = require('node:fs/promises');
const { resolve, join } = require('node:path');

const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/leiah.js', ['leiah.js', 'text/javascript; charset=utf-8']],
  ['/script.js', ['script.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
]);

const base = `FROM SKYNET_questao q
  JOIN SKYNET_fase f ON f.id_fase = q.id_fase_id
  JOIN SKYNET_edicao e ON e.id_edicao = f.id_edicao_id
  JOIN SKYNET_tipo_questao t ON t.id_tipo_questao = q.id_tipo_questao_id`;
const historicYear = `COALESCE(
  (SELECT MIN(a.ano) FROM SKYNET_questao_anos qa JOIN SKYNET_ano_historico a
    ON a.id_ano = qa.ano_historico_id WHERE qa.questao_id = q.id_questao),
  (SELECT (CAST(p.seculo AS INTEGER) - 1) * 100 + 50
    FROM SKYNET_questao_periodos qp JOIN SKYNET_periodo p ON p.id_periodo = qp.periodo_id
    WHERE qp.questao_id = q.id_questao AND CAST(p.seculo AS INTEGER) > 0
    ORDER BY p.id_periodo LIMIT 1))`;
const phaseLabel = `CASE
  WHEN f.tipo_fase IN ('Ensino Fundamental', 'Ensino Médio') THEN f.num_fase || 'ª fase · ' || f.tipo_fase
  WHEN f.tipo_fase LIKE '%final%' THEN 'Fase Final'
  WHEN f.tipo_fase LIKE '%desafio%' THEN 'Desafio'
  WHEN f.tipo_fase LIKE '%introdut%' THEN 'Fase Introdutória'
  ELSE f.num_fase || 'ª fase' END`;
const defaultOrder = 'e.ano_edicao DESC, f.num_fase, q.num_questao, q.id_questao';
const catalog = {
  categorias: 'SELECT id_tipo_questao AS id, tipo_nome AS nome FROM SKYNET_tipo_questao ORDER BY tipo_nome',
  fases: 'SELECT id_fase AS id, num_fase AS num, tipo_fase AS tipo, id_edicao_id AS id_edicao FROM SKYNET_fase ORDER BY num_fase',
  edicoes: 'SELECT id_edicao AS id, num_edicao AS num, ano_edicao AS ano FROM SKYNET_edicao ORDER BY ano_edicao DESC',
  periodos: 'SELECT id_periodo AS id, periodo_nome AS nome, seculo FROM SKYNET_periodo ORDER BY periodo_nome',
  tags: 'SELECT id_tag AS id, tag_nome AS nome FROM SKYNET_tag ORDER BY tag_nome',
};
const relations = [
  ['documentos', 'documento', 'documento_id', 'id_documento', 'r.nome_documento AS nome, r.tipo_documento AS tipo'],
  ['periodos', 'periodo', 'periodo_id', 'id_periodo', 'r.periodo_nome AS nome, r.seculo'],
  ['tags', 'tag', 'tag_id', 'id_tag', 'r.tag_nome AS nome'],
  ['anos', 'ano_historico', 'ano_historico_id', 'id_ano', 'r.ano'],
];

class InvalidQuery extends Error {}

function integer(params, key, fallback) {
  const raw = params.get(key);
  if (raw === null || raw === '') return fallback;
  const value = Number(raw);
  if (!/^-?\d+$/.test(raw) || !Number.isSafeInteger(value)) {
    throw new InvalidQuery(`Parâmetro inválido: ${key}.`);
  }
  return value;
}

function filtersFor(params) {
  const clauses = [];
  const values = [];
  function add(sql, ...args) { clauses.push(`(${sql})`); values.push(...args); }
  const term = params.get('q')?.trim();
  if (term) add(`instr(normalizar(q.enunciado), normalizar(?)) > 0
    OR EXISTS (SELECT 1 FROM SKYNET_item i WHERE i.id_questao_id = q.id_questao AND instr(normalizar(i.texto), normalizar(?)) > 0)
    OR EXISTS (SELECT 1 FROM SKYNET_questao_documentos qd JOIN SKYNET_documento d ON d.id_documento = qd.documento_id
      WHERE qd.questao_id = q.id_questao AND instr(normalizar(d.nome_documento || ' ' || COALESCE(d.tipo_documento, '')), normalizar(?)) > 0)
    OR EXISTS (SELECT 1 FROM SKYNET_questao_tags qt JOIN SKYNET_tag tg ON tg.id_tag = qt.tag_id
      WHERE qt.questao_id = q.id_questao AND instr(normalizar(tg.tag_nome), normalizar(?)) > 0)
    OR EXISTS (SELECT 1 FROM SKYNET_questao_periodos qp JOIN SKYNET_periodo p ON p.id_periodo = qp.periodo_id
      WHERE qp.questao_id = q.id_questao AND instr(normalizar(p.periodo_nome), normalizar(?)) > 0)`, term, term, term, term, term);
  const category = params.get('categoria');
  const phase = params.get('fase');
  const period = params.get('periodo');
  const edition = params.get('edicao');
  if (category) add('t.tipo_nome = ?', category);
  if (phase) add(`${phaseLabel} = ? OR f.tipo_fase = ? OR CAST(f.num_fase AS TEXT) = ?`, phase, phase, phase);
  if (period) add(`EXISTS (SELECT 1 FROM SKYNET_questao_periodos qp JOIN SKYNET_periodo p ON p.id_periodo = qp.periodo_id
    WHERE qp.questao_id = q.id_questao AND p.periodo_nome = ?)`, period);
  if (edition) add('e.ano_edicao = ?', edition);
  const tags = [...new Set(params.getAll('tag'))];
  if (tags.length > 100) throw new InvalidQuery('Selecione até 100 tags.');
  for (const tag of tags) {
    const id = integer(new URLSearchParams({ tag }), 'tag', null);
    if (id === null || id < 1) throw new InvalidQuery('Tag inválida.');
    add('EXISTS (SELECT 1 FROM SKYNET_questao_tags qt WHERE qt.questao_id = q.id_questao AND qt.tag_id = ?)', id);
  }
  const start = integer(params, 'yearStart', null);
  const end = integer(params, 'yearEnd', null);
  if (start !== null && end !== null && start > end) throw new InvalidQuery('O ano inicial deve ser menor ou igual ao ano final.');
  if (start !== null) add(`${historicYear} >= ?`, start);
  if (end !== null) add(`${historicYear} <= ?`, end);
  return { where: clauses.length ? `WHERE ${clauses.join(' AND ')}` : '', values };
}

function questionsFor(db, params, schema) {
  const requestedPage = Math.max(1, integer(params, 'page', 1));
  const pageSize = Math.min(100, Math.max(1, integer(params, 'pageSize', 6)));
  const { where, values } = filtersFor(params);
  const sort = params.get('sort');
  const order = sort === 'yearAsc' ? 'ano_historico ASC NULLS LAST, ' :
    sort === 'yearDesc' ? 'ano_historico DESC NULLS LAST, ' : '';
  const total = db.prepare(`SELECT COUNT(*) AS total ${base} ${where}`).get(...values).total;
  const page = Math.min(requestedPage, Math.max(1, Math.ceil(total / pageSize)));
  const provenance = schema.provenance
    ? 'q.sufixo, q.prova_pdf, q.prova_pagina, q.gabarito_pdf'
    : "'' AS sufixo, NULL AS prova_pdf, NULL AS prova_pagina, NULL AS gabarito_pdf";
  const questions = db.prepare(`SELECT q.id_questao AS id, q.num_questao AS num, q.enunciado, ${provenance},
      q.id_fase_id AS id_fase, q.id_tipo_questao_id AS id_tipo, f.num_fase AS fase_num,
      f.tipo_fase AS fase_tipo, f.id_edicao_id AS id_edicao, e.num_edicao AS edicao_num,
      e.ano_edicao AS edicao_ano, t.tipo_nome, ${historicYear} AS ano_historico
    ${base} ${where} ORDER BY ${order}${defaultOrder} LIMIT ? OFFSET ?`
  ).all(...values, pageSize, (page - 1) * pageSize);
  if (questions.length) {
    const byId = new Map(questions.map(q => [q.id, q]));
    const ids = [...byId.keys()];
    const marks = ids.map(() => '?').join(',');
    for (const q of questions) {
      for (const [key] of relations) q[key] = [];
      q.itens = [];
    }
    for (const [key, table, fk, id, columns] of relations) {
      const extra = key === 'documentos' && schema.documents
        ? ", r.arquivo_fonte, r.pagina_fonte, length(COALESCE(r.texto_fonte, '')) > 0 AS tem_texto" : '';
      for (const { questao_id, ...row } of db.prepare(`SELECT l.questao_id, r.${id} AS id, ${columns}${extra}
        FROM SKYNET_questao_${key} l JOIN SKYNET_${table} r ON r.${id} = l.${fk}
        WHERE l.questao_id IN (${marks}) ORDER BY r.${id}`).all(...ids)) {
        byId.get(questao_id)[key].push(row);
      }
    }
    const points = schema.pending ? 'CASE WHEN p.item_id IS NULL THEN i.pontuacao END' : 'i.pontuacao';
    const pending = schema.pending ? 'LEFT JOIN SKYNET_item_pontuacao_pendente p ON p.item_id = i.id_item' : '';
    for (const item of db.prepare(`SELECT i.id_questao_id AS id_questao, i.id_item AS id, i.identificador, ${points} AS pontuacao, i.texto
      FROM SKYNET_item i ${pending} WHERE i.id_questao_id IN (${marks}) ORDER BY i.identificador, i.id_item`).all(...ids)) {
      byId.get(item.id_questao).itens.push(item);
    }
    if (schema.media) {
      const documents = new Map();
      for (const q of questions) for (const doc of q.documentos) {
        if (!documents.has(doc.id)) documents.set(doc.id, { urls: [], imagens: [] });
        Object.assign(doc, documents.get(doc.id));
      }
      for (const row of db.prepare(`SELECT DISTINCT u.documento_id, u.url
        FROM SKYNET_documento_url u JOIN SKYNET_questao_documentos qd USING (documento_id)
        WHERE qd.questao_id IN (${marks}) ORDER BY u.url`).all(...ids)) {
        documents.get(row.documento_id).urls.push(row.url);
      }
      for (const row of db.prepare(`SELECT DISTINCT m.documento_id, m.imagem_hash
        FROM SKYNET_documento_imagem m JOIN SKYNET_questao_documentos qd USING (documento_id)
        WHERE qd.questao_id IN (${marks}) ORDER BY m.rowid`).all(...ids)) {
        documents.get(row.documento_id).imagens.push(`/api/imagens/${row.imagem_hash}`);
      }
    }
  }
  return { page, pageSize, total, questions };
}

function send(res, status, body, type = 'application/json; charset=utf-8', cache = 'no-store') {
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': cache, 'X-Content-Type-Options': 'nosniff' });
  res.end(type.startsWith('application/json') ? JSON.stringify(body) : body);
}

function createSite(database = resolve(process.env.DATABASE_PATH || join(__dirname, 'dados/db.sqlite3'))) {
  const { DatabaseSync } = require('node:sqlite');
  const db = new DatabaseSync(database, { readOnly: true, timeout: 2000 });
  let catalogStatements, schema;
  try {
    db.prepare(`SELECT 1 ${base} LIMIT 1`).get();
    catalogStatements = Object.entries(catalog).map(([key, sql]) => [key, db.prepare(sql)]);
    const hasColumn = (table, column) => db.prepare(`PRAGMA table_info(${table})`).all().some(r => r.name === column);
    const hasTable = name => Boolean(db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(name));
    schema = {
      documents: hasColumn('SKYNET_documento', 'texto_fonte'),
      provenance: hasColumn('SKYNET_questao', 'sufixo'),
      pending: hasTable('SKYNET_item_pontuacao_pendente'),
      media: ['SKYNET_documento_url', 'SKYNET_documento_imagem', 'SKYNET_imagem'].every(hasTable),
    };
    db.function('normalizar', { deterministic: true }, value => value == null ? '' :
      String(value).normalize('NFD').replace(/\p{M}/gu, '').toLowerCase());
  } catch (error) {
    db.close();
    throw error;
  }
  const server = createServer(async (req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.setHeader('Allow', 'GET, HEAD');
      return send(res, 405, { error: 'Método não permitido.' });
    }
    try {
      const { pathname, searchParams } = new URL(req.url, 'http://localhost');
      if (pathname === '/api/filtros') {
        return send(res, 200, Object.fromEntries(catalogStatements.map(([key, stmt]) => [key, stmt.all()])));
      }
      const document = /^\/api\/documentos\/([1-9][0-9]{0,14})$/.exec(pathname);
      if (document && schema.documents) {
        const row = db.prepare('SELECT texto_fonte AS texto FROM SKYNET_documento WHERE id_documento = ?').get(Number(document[1]));
        return row ? send(res, 200, row) : send(res, 404, { error: 'Documento não encontrado.' });
      }
      const image = /^\/api\/imagens\/([a-f0-9]{64})$/.exec(pathname);
      if (image && schema.media) {
        const row = db.prepare(`SELECT i.conteudo FROM SKYNET_imagem i WHERE i.hash = ?
          AND EXISTS (SELECT 1 FROM SKYNET_documento_imagem m JOIN SKYNET_questao_documentos q USING (documento_id)
          WHERE m.imagem_hash = i.hash)`).get(image[1]);
        return row ? send(res, 200, row.conteudo, 'image/jpeg', 'public, max-age=31536000, immutable')
          : send(res, 404, { error: 'Imagem não encontrada.' });
      }
      if (pathname === '/api/questoes') {
        db.exec('BEGIN');
        let payload;
        try {
          payload = questionsFor(db, searchParams, schema);
          db.exec('COMMIT');
        } catch (error) {
          db.exec('ROLLBACK');
          throw error;
        }
        return send(res, 200, payload);
      }
      const asset = assets.get(pathname);
      if (!asset) return send(res, 404, { error: 'Página não encontrada.' });
      const [file, type] = asset;
      send(res, 200, await readFile(join(__dirname, 'web', file)), type);
    } catch (error) {
      if (error instanceof InvalidQuery) return send(res, 400, { error: error.message });
      console.error(error);
      send(res, 500, { error: 'Não foi possível atender à solicitação.' });
    }
  });
  server.once('close', () => db.close());
  return server;
}

if (require.main === module) {
  if (Number(process.versions.node.split('.')[0]) < 24) {
    console.error('Use Node.js 24 ou superior para executar o site.');
    process.exit(1);
  }
  const host = process.env.HOST || '127.0.0.1';
  const port = Number(process.env.PORT || 8000);
  try {
    createSite().listen(port, host, () => console.log(`Site e API em http://${host}:${port}`));
  } catch (error) {
    console.error('Não foi possível abrir o banco SQLite existente:', error.message);
    process.exitCode = 1;
  }
}

module.exports = { createSite };

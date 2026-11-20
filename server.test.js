'use strict';

const assert = require('node:assert/strict');
const { once } = require('node:events');
const { mkdtemp, copyFile, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const { test } = require('node:test');
const { createSite } = require('./server');

test('site e API consultam o SQLite existente', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'onhb-'));
  const database = join(dir, 'db.sqlite3');
  t.after(() => rm(dir, { recursive: true, force: true }));
  await copyFile(process.env.ONHB_TEST_DB || join(__dirname, 'dados/db.sqlite3'), database);
  const site = createSite(database).listen(0, '127.0.0.1');
  await once(site, 'listening');
  try {
    const root = `http://127.0.0.1:${site.address().port}`;
    const get = path => fetch(root + path);
    const json = async path => {
      const response = await get(path);
      assert.equal(response.status, 200);
      return response.json();
    };
    const index = await get('/');
    assert.equal(index.status, 200);
    assert.match(await index.text(), /<script src="leiah\.js/);
    assert.match((await get('/leiah.js')).headers.get('content-type'), /javascript/);
    assert.equal((await get('/dados/db.sqlite3')).status, 404);
    assert.equal((await get('/server.js')).status, 404);
    assert.equal((await get('/api/questoes?page=abc')).status, 400);
    assert.equal((await get('/api/questoes?yearStart=1900&yearEnd=1800')).status, 400);

    const filters = await json('/api/filtros');
    for (const values of Object.values(filters)) assert.ok(values.length);
    const first = await json('/api/questoes');
    const second = await json('/api/questoes?page=2');
    assert.equal(first.pageSize, 6);
    assert.equal(first.questions.length, 6);
    assert.equal(second.page, 2);
    assert.equal(first.total, second.total);
    assert.ok(second.questions.every(q => !first.questions.some(p => p.id === q.id)));
    for (const q of first.questions) {
      for (const key of ['documentos', 'periodos', 'tags', 'anos', 'itens']) assert.ok(Array.isArray(q[key]));
    }
    const phase = await json('/api/questoes?fase=Fase+Final');
    assert.ok(phase.questions.length);
    assert.ok(phase.questions.every(q => /final/i.test(q.fase_tipo)));
    const withTags = (await json('/api/questoes?edicao=2024&pageSize=100')).questions.find(q => q.tags.length);
    const tag = withTags.tags[0];
    const tagged = await json('/api/questoes?tag=' + tag.id);
    assert.ok(tagged.questions.length);
    assert.ok(tagged.questions.every(q => q.tags.some(item => item.id === tag.id)));
    const sorted = await json('/api/questoes?yearStart=1500&yearEnd=1900&sort=yearDesc');
    const years = sorted.questions.map(q => q.ano_historico);
    assert.ok(years.length);
    assert.ok(years.every(y => y >= 1500 && y <= 1900));
    assert.deepEqual(years, years.toSorted((a, b) => b - a));

    const onhb18 = await json('/api/questoes?edicao=2026&pageSize=100');
    const first2026 = onhb18.questions.find(q => q.fase_num === 1 && q.num === 1);
    assert.equal(onhb18.total, 40);
    assert.deepEqual(first2026.itens.map(item => item.pontuacao), [1, 0, 5, 4]);
    assert.match(first2026.gabarito_pdf, /Gabarito_18a_ONHB/);
    assert.deepEqual((await json('/api/questoes?edicao=2026&fase=5%C2%AA+fase+%C2%B7+Ensino+Fundamental'))
      .questions.map(q => q.num), [101]);
    const onhb17 = await json('/api/questoes?edicao=2025&fase=1');
    const halloween = onhb17.questions.find(q => q.num === 1).documentos.find(d => /Halloween/.test(d.nome));
    assert.ok(halloween.imagens.length);
    assert.ok(halloween.urls.some(url => url.startsWith('https://')));
    assert.match((await json(`/api/documentos/${halloween.id}`)).texto, /Luis Fernando Cazo/);
    const image = await get(halloween.imagens[0]);
    assert.equal(image.status, 200);
    assert.match(image.headers.get('content-type'), /^image\/jpeg/);
    assert.deepEqual([...new Uint8Array(await image.arrayBuffer()).slice(0, 2)], [255, 216]);
    assert.equal((await get('/api/imagens/' + '0'.repeat(64))).status, 404);

    const q = first.questions[0];
    const editor = new DatabaseSync(database);
    try {
      editor.prepare('UPDATE SKYNET_questao SET enunciado = ? WHERE id_questao = ?')
        .run('Verificação Ímpar de atualização ONHB', q.id);
    } finally {
      editor.close();
    }
    const updated = await json('/api/questoes?q=' + encodeURIComponent('VERIFICACAO IMPAR de atualizacao ONHB'));
    assert.equal(updated.total, 1);
    assert.equal(updated.questions[0].id, q.id);
  } finally {
    await new Promise((resolve, reject) => site.close(error => error ? reject(error) : resolve()));
  }
});

# Repositório ONHB

O Node.js serve o site (`web/`) e a API no mesmo endereço. A API abre `dados/db.sqlite3` somente para leitura: não cria tabelas, não importa CSVs e não regrava dados na inicialização. O navegador carrega os filtros e pede até 15 questões por página no celular e seis nas demais telas; busca, filtros, ordenação e paginação são feitos no servidor.

## Executar localmente

Com **Node.js 24 ou superior**, entre na pasta `ONHB_Network-main` e rode:

```bash
npm start
```

Abra `http://127.0.0.1:8000`. Não precisa instalar dependências npm, Python ou Django. Não abra `index.html` diretamente: o site usa `/api/questoes` e `/api/filtros`. Para verificar o servidor e a API, rode `npm test`.

## Hospedar

1. Use um servidor ou serviço que execute Node.js continuamente e tenha armazenamento persistente. GitHub Pages sozinho não executa a API.
2. Envie `package.json`, `server.js`, `web/` e o banco `dados/db.sqlite3`. Se usar Git para implantar, confira se o banco foi incluído ou envie-o separadamente. Os arquivos de `legado/`, os CSVs e os backups não são necessários no servidor.
3. Configure `npm start` como comando de inicialização. Use `HOST=0.0.0.0` se a hospedagem precisar chegar ao processo pela rede; mantenha o padrão `127.0.0.1` se usar um proxy reverso no mesmo servidor. A porta padrão é `8000` e pode ser definida por `PORT`. Para servir em produção, aponte o domínio para o serviço ou use um proxy reverso com HTTPS.
4. Se o banco estiver em um volume persistente, aponte para ele com `DATABASE_PATH=/caminho/para/db.sqlite3`. O Node serve somente os arquivos públicos listados em `server.js`, não o arquivo SQLite.

Exemplo de execução em uma hospedagem com volume persistente:

```bash
HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/dados/onhb/db.sqlite3 npm start
```

## Atualizar os dados

Faça um backup e edite o SQLite com sua ferramenta de preferência, preservando as tabelas e relações existentes. Alterações salvas **no arquivo em uso** aparecem nas próximas consultas; recarregue a página para atualizar também as opções dos filtros. Não precisa gerar JavaScript nem reimplantar o site. Se for **substituir** o arquivo `.sqlite3` por uma cópia atualizada, pare o serviço, troque o arquivo pelo backup consistente atualizado e inicie o serviço novamente.

## Acervo de provas e gabaritos

O [relatório por edição e fase](dados/importacao-onhb.md) registra o que foi importado e o que ainda precisa de revisão. Em **Ver completa**, o site mostra os pontos de cada alternativa, o PDF de origem e os documentos vinculados. Imagens, textos e links dos documentos abrem no próprio cartão da fonte. As imagens ficam no SQLite, portanto basta enviar o banco atualizado na hospedagem; não é necessário enviar os PDFs originais.

Para repetir a importação em **uma cópia** do banco, use Python com `pymupdf` e `pdftotext` (Poppler). Esses programas são necessários só para importar, não para rodar o site:

```bash
cp dados/db.sqlite3 /tmp/onhb-importacao.sqlite3
python3 scripts/importar_onhb.py ~/Downloads/onhbrepositorio/ONHB \
  /tmp/onhb-importacao.sqlite3 --relatorio /tmp/onhb-importacao.md
```

Use `--edicao 18` para processar apenas uma edição. Para incluir os fac-símiles das fases digitalizadas de 2012, gere o cache com `python3 scripts/ocr_onhb.py ~/Downloads/onhbrepositorio/ONHB/4_ONHB_2012 /tmp/onhb-ocr` (Tesseract com idioma `por`) e acrescente `--ocr-cache /tmp/onhb-ocr` ao importador. O OCR foi usado para identificar páginas, não para substituir automaticamente o enunciado ou a pontuação.

/* LEIAH - Repositorio de Questoes de Historia e ONHB */

var filtrosAPI = null;
var controllerBusca = null;
var mobileLayout = window.matchMedia("(width < 640px)");
var mobileQuestionTrigger = null;

function esc(valor) {
  return String(valor == null ? "" : valor)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function unico(arr) {
  var vistos = {};
  return arr.filter(function (v) {
    if (v == null || v === "" || vistos[v]) return false;
    vistos[v] = true;
    return true;
  });
}

var PERIODO_DISPLAY = {
  Colonial: "Brasil Colonial",
  Imperial: "Brasil Império",
  Republicano: "Brasil República",
  Republlicano: "Brasil República"
};

function periodoLabel(nome) {
  return PERIODO_DISPLAY[nome] || nome;
}

function faseLabel(f) {
  if (!f) return "Fase";
  var t = f.tipo || "";
  if (/^Ensino (Fundamental|Médio)$/.test(t)) return f.num + "ª fase · " + t;
  if (/final/i.test(t)) return "Fase Final";
  if (/desafio/i.test(t)) return "Desafio";
  if (/introdut/i.test(t)) return "Fase Introdutória";
  return f.num + "ª fase";
}

function montarQuestao(q) {
  var fase = { id: q.id_fase, num: q.fase_num, tipo: q.fase_tipo };
  var docs = q.documentos;
  var pers = q.periodos;
  var tgs = q.tags;
  var ans = q.anos;
  var itens = q.itens;

  var anosList = ans.map(function (a) { return a.ano; })
    .filter(function (n) { return !isNaN(n); })
    .sort(function (a, b) { return a - b; });

  var periodNames = unico(pers.map(function (p) { return p.nome; }));
  var docTipos = unico(docs.map(function (d) { return d.tipo; }));
  var docNomes = unico(docs.map(function (d) { return d.nome; }));
  var tagNames = unico(tgs.map(function (t) { return t.nome; }));

  var edicaoAno = q.edicao_ano;
  var tipoNome = q.tipo_nome;
  var phase = faseLabel(fase);
  var edition = edicaoAno + " (" + q.edicao_num + "ª Edição)";

  var date = anosList.length
    ? anosList.join(", ")
    : (pers.length ? pers.map(function (p) { return p.seculo + "00"; }).join(", ")
      : "Datação não informada");

  var descFull = q.enunciado || "";
  var numero = String(q.num) + (q.sufixo || "");
  var imagem = docs.flatMap(function (doc) { return doc.imagens || []; })[0];
  return {
    id: q.id,
    num: numero,
    headline: (tipoNome === "Tarefa" ? "Tarefa " : "Questão ") + numero + " · " + (tipoNome || "Questão"),
    title: descFull.length > 90 ? descFull.slice(0, 90) + "…" : descFull,
    desc: descFull,
    descShort: descFull.length > 150 ? descFull.slice(0, 150) + "…" : descFull,
    edition: edition,
    edicaoAno: edicaoAno,
    phase: phase,
    category: tipoNome || "Questão",
    historicYear: q.ano_historico,
    period: periodNames.map(periodoLabel).join(", "),
    tags: tagNames,
    docNomes: docNomes,
    documentos: docs,
    prova: q.prova_pdf,
    pagina: q.prova_pagina,
    gabarito: q.gabarito_pdf,
    itens: itens,
    author: docNomes.length ? docNomes[0] : "Acervo ONHB",
    date: date,
    type: docTipos.join(", ") || "Fonte histórica",
    origin: docNomes.join("; ") || "Acervo ONHB",
    language: "Português",
    img: imagem || null
  };
}

var QUESTIONS_DATA = [];

/* ================= Estado ================= */

var currentSelectedId = null;
var modalSelectedId = null;
var currentPage = 1;
var itemsPerPage = 6;

var filterState = {
  searchQuery: "",
  categoria: "all",
  fase: "all",
  periodo: "all",
  edicao: "all",
  yearStart: null,
  yearEnd: null,
  tags: [],
  sort: "relevant"
};

function encontrarQuestao(id) {
  return QUESTIONS_DATA.find(function (q) { return q.id === id; });
}

/* ================= Popular filtros dinamicamente ================= */

function montarOpcoes(menu, itens, rotuloTodos) {
  if (!menu) return;
  var html = '<div class="custom-option selected" data-value="all">' +
    esc(rotuloTodos) + "</div>";
  itens.forEach(function (o) {
    html += '<div class="custom-option" data-value="' + esc(o.value) + '">' +
      esc(o.label) + "</div>";
  });
  menu.innerHTML = html;
}

function popularFiltros() {
  var categorias = filtrosAPI.categorias;
  var fasesDisponiveis = filtrosAPI.fases;
  var periodosDisponiveis = filtrosAPI.periodos;
  var edicoesDisponiveis = filtrosAPI.edicoes;
  var tagsDisponiveis = filtrosAPI.tags;
  categorias = unico(categorias.map(function (tipo) { return tipo.nome; }))
    .sort(function (a, b) { return a.localeCompare(b, "pt"); });
  montarOpcoes(document.querySelector("#wrapperCategoria .custom-options-menu"),
    categorias.map(function (nome) { return { value: nome, label: nome }; }), "Todas as categorias");

  var fasesMap = new Map();
  (fasesDisponiveis || []).forEach(function (f) {
    var label = faseLabel(f);
    if (!fasesMap.has(label)) {
      fasesMap.set(label, { value: label, label: label, num: f.num });
    }
  });
  var fases = Array.from(fasesMap.values()).sort(function (a, b) {
    return a.num - b.num || a.label.localeCompare(b.label);
  });
  montarOpcoes(document.querySelector("#wrapperFase .custom-options-menu"), fases, "Todas as fases");

  var perMap = new Map();
  (periodosDisponiveis || []).forEach(function (p) {
    if (p.nome && !perMap.has(p.nome)) {
      perMap.set(p.nome, { value: p.nome, label: periodoLabel(p.nome) });
    }
  });
  montarOpcoes(document.querySelector("#wrapperPeriodo .custom-options-menu"),
    Array.from(perMap.values()), "Todos os períodos");

  var eds = (edicoesDisponiveis || []).slice()
    .sort(function (a, b) { return b.ano - a.ano; })
    .map(function (e) { return { value: String(e.ano), label: e.ano + " (" + e.num + "ª Edição)" }; });
  montarOpcoes(document.querySelector("#wrapperEdicao .custom-options-menu"), eds, "Todas as edições");

  var cont = document.getElementById("tagsFilterContainer");
  if (cont) {
    var tags = unico((tagsDisponiveis || []).map(function (t) { return t.nome; }))
      .sort(function (a, b) { return a.localeCompare(b, "pt"); });
    cont.innerHTML = tags.map(function (t, i) {
      var id = "tagFiltro" + i;
      return '<div><input type="checkbox" id="' + id +
        '" class="hidden filter-tag-checkbox" value="' + esc(t) + '">' +
        '<label for="' + id + '" class="flex items-center gap-1.5 px-2.5 py-2 rounded-lg border border-gray-700 text-gray-200 bg-[#181916] cursor-pointer font-medium transition-all hover:bg-gray-700 select-none text-xs">' +
        '<i class="fa-solid fa-check text-[10px] hidden"></i> ' + esc(t) + "</label></div>";
    }).join("");

    cont.addEventListener("change", function (e) {
      var cb = e.target;
      if (!cb.classList || !cb.classList.contains("filter-tag-checkbox")) return;
      var icon = cb.nextElementSibling && cb.nextElementSibling.querySelector("i");
      if (!icon) return;
      if (cb.checked) icon.classList.remove("hidden");
      else icon.classList.add("hidden");
    });
  }
}

/* ================= Dropdowns animados (do layout) ================= */

function openMobilePanel(dialogId, panelId) {
  var dialog = document.getElementById(dialogId);
  if (!mobileLayout.matches || dialog.open) return;
  dialog.appendChild(document.getElementById(panelId));
  dialog.showModal();
  dialog.scrollTop = 0;
}

function setupMobilePanels() {
  var filtersButton = document.getElementById("mobileFiltersBtn");

  filtersButton.addEventListener("click", function () {
    openMobilePanel("mobileFilters", "filterPanel");
    filtersButton.setAttribute("aria-expanded", "true");
  });

  [
    ["mobileFilters", "filterPanel", "filterSidebar"],
    ["mobileMetadata", "metadataPanel", "metadataSidebar"]
  ].forEach(function (ids) {
    var dialog = document.getElementById(ids[0]);
    var panel = document.getElementById(ids[1]);
    var sidebar = document.getElementById(ids[2]);
    panel.querySelector(".mobile-panel-close").addEventListener("click", function () {
      dialog.close();
    });
    dialog.addEventListener("click", function (event) {
      if (event.target !== dialog) return;
      var rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX >= rect.right ||
          event.clientY < rect.top || event.clientY >= rect.bottom) dialog.close();
    });
    dialog.addEventListener("close", function () {
      if (dialog.open) return;
      sidebar.appendChild(panel);
      if (dialog.id === "mobileFilters") {
        filtersButton.setAttribute("aria-expanded", "false");
        panel.querySelectorAll(".custom-select-wrapper.open").forEach(function (wrapper) {
          wrapper.classList.remove("open");
        });
      }
    });
    mobileLayout.addEventListener("change", function () {
      if (!mobileLayout.matches && dialog.open) dialog.close();
    });
  });
}

function setupCustomAnimatedDropdowns() {
  var wrappers = document.querySelectorAll(".custom-select-wrapper");

  wrappers.forEach(function (wrapper) {
    var trigger = wrapper.querySelector(".custom-select-trigger");
    var selectedText = trigger.querySelector(".selected-text");
    if (!selectedText || trigger.dataset.initialized) return;
    trigger.dataset.initialized = "true";

    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      wrappers.forEach(function (w) { if (w !== wrapper) w.classList.remove("open"); });
      wrapper.classList.toggle("open");
    });

    wrapper.addEventListener("click", function (e) {
      var opt = e.target.closest(".custom-option");
      if (!opt || !wrapper.contains(opt)) return;
      e.stopPropagation();
      wrapper.querySelectorAll(".custom-option").forEach(function (o) { o.classList.remove("selected"); });
      opt.classList.add("selected");

      var val = opt.getAttribute("data-value");
      trigger.setAttribute("data-value", val);
      selectedText.textContent = opt.textContent.trim();
      wrapper.classList.remove("open");

      if (wrapper.id === "wrapperCategoria") filterState.categoria = val;
      if (wrapper.id === "wrapperFase") filterState.fase = val;
      if (wrapper.id === "wrapperPeriodo") filterState.periodo = val;
      if (wrapper.id === "wrapperEdicao") filterState.edicao = val;
      if (wrapper.id === "wrapperSort") filterState.sort = val;
      currentPage = 1;
      pesquisarNoBanco();
    });
  });

  if (!document.documentElement.dataset.dropdownCloseInitialized) {
    document.documentElement.dataset.dropdownCloseInitialized = "true";
    document.addEventListener("click", function () {
      wrappers.forEach(function (w) { w.classList.remove("open"); });
    });
  }
}

function resetCustomDropdowns() {
  document.querySelectorAll(".custom-select-wrapper").forEach(function (wrapper) {
    var firstOpt = wrapper.querySelector(".custom-option");
    if (!firstOpt) return;
    wrapper.querySelectorAll(".custom-option").forEach(function (o) { o.classList.remove("selected"); });
    firstOpt.classList.add("selected");
    var trigger = wrapper.querySelector(".custom-select-trigger");
    var selectedText = trigger.querySelector(".selected-text");
    trigger.setAttribute("data-value", firstOpt.getAttribute("data-value"));
    selectedText.textContent = firstOpt.textContent.trim();
  });
}

/* ================= Inicializacao ================= */

document.addEventListener("DOMContentLoaded", function () {
  setupMobilePanels();
  setupCustomAnimatedDropdowns();

  var scrollObserver = new IntersectionObserver(function (entries, observer) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("active");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach(function (el) { scrollObserver.observe(el); });

  var searchWrapper = document.getElementById("searchWrapper");
  var searchInput = document.getElementById("mainSearchInput");
  var searchIconBtn = document.getElementById("searchIconBtn");
  var searchCloseBtn = document.getElementById("searchCloseBtn");
  var quickSearchBtn = document.getElementById("quickSearchBtn");

  function closeSearch() {
    searchWrapper.classList.remove("active");
    searchInput.value = "";
    filterState.searchQuery = "";
    currentPage = 1;
    pesquisarNoBanco();
  }

  searchIconBtn && searchIconBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    if (!searchWrapper.classList.contains("active")) {
      searchWrapper.classList.add("active");
      setTimeout(function () { searchInput.focus(); }, 250);
      searchWrapper.scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      filterState.searchQuery = searchInput.value.trim();
      currentPage = 1;
      pesquisarNoBanco();
    }
  });

  searchCloseBtn && searchCloseBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    closeSearch();
  });

  searchInput && searchInput.addEventListener("keyup", function (e) {
    if (e.key === "Enter") {
      filterState.searchQuery = searchInput.value.trim();
      currentPage = 1;
      pesquisarNoBanco();
    }
  });

  quickSearchBtn && quickSearchBtn.addEventListener("click", function () {
    searchWrapper.classList.add("active");
    searchWrapper.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(function () { searchInput.focus(); }, 300);
  });

  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      searchWrapper.classList.add("active");
      searchWrapper.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(function () { searchInput.focus(); }, 300);
    }
  });

  document.getElementById("btnApplyFilters").addEventListener("click", function () {
    var yStart = parseInt(document.getElementById("yearStart").value, 10);
    var yEnd = parseInt(document.getElementById("yearEnd").value, 10);
    filterState.yearStart = isNaN(yStart) ? null : yStart;
    filterState.yearEnd = isNaN(yEnd) ? null : yEnd;
    filterState.tags = Array.from(document.querySelectorAll(".filter-tag-checkbox:checked"))
      .map(function (cb) { return cb.value; });
    currentPage = 1;
    var filterDrawer = document.getElementById("mobileFilters");
    if (filterDrawer.open) filterDrawer.close();
    pesquisarNoBanco();
    showNotification("Filtros aplicados com sucesso!");
  });

  document.getElementById("btnResetFilters").addEventListener("click", function () {
    resetCustomDropdowns();
    document.getElementById("yearStart").value = "";
    document.getElementById("yearEnd").value = "";
    document.querySelectorAll(".filter-tag-checkbox").forEach(function (cb) {
      cb.checked = false;
      var icon = cb.nextElementSibling && cb.nextElementSibling.querySelector("i");
      icon && icon.classList.add("hidden");
    });
    filterState = {
      searchQuery: "", categoria: "all", fase: "all", periodo: "all", edicao: "all",
      yearStart: null, yearEnd: null, tags: [], sort: "relevant"
    };
    if (searchWrapper.classList.contains("active")) {
      closeSearch();
    } else {
      currentPage = 1;
      pesquisarNoBanco();
    }
    showNotification("Filtros redefinidos!");
  });

  document.getElementById("metaActionBtn").addEventListener("click", function () {
    openQuestionModal(currentSelectedId);
  });

  fetch("/api/filtros").then(function (response) {
    if (!response.ok) throw new Error("Não foi possível consultar os filtros no banco de dados.");
    return response.json();
  }).then(function (filtros) {
    filtrosAPI = filtros;
    popularFiltros();
  }).catch(function (error) {
    showNotification(error.message);
  });
  pesquisarNoBanco();
});

/* ================= Filtragem e render ================= */

function montarQueryBanco() {
  var params = new URLSearchParams();
  if (filterState.searchQuery) params.set("q", filterState.searchQuery);
  if (filterState.categoria !== "all") params.set("categoria", filterState.categoria);
  if (filterState.fase !== "all") params.set("fase", filterState.fase);
  if (filterState.periodo !== "all") params.set("periodo", filterState.periodo);
  if (filterState.edicao !== "all") params.set("edicao", filterState.edicao);
  if (filterState.yearStart != null) params.set("yearStart", filterState.yearStart);
  if (filterState.yearEnd != null) params.set("yearEnd", filterState.yearEnd);
  filterState.tags.forEach(function (tag) {
    var record = filtrosAPI && filtrosAPI.tags.find(function (item) { return item.nome === tag; });
    if (record) params.append("tag", record.id);
  });
  params.set("page", currentPage);
  params.set("pageSize", itemsPerPage);
  params.set("sort", filterState.sort);
  return params.toString();
}

async function pesquisarNoBanco() {
  if (controllerBusca) controllerBusca.abort();
  var controller = new AbortController();
  controllerBusca = controller;
  var count = document.getElementById("totalQuestionsCount");
  count.textContent = "Consultando questões…";
  try {
    var response = await fetch("/api/questoes?" + montarQueryBanco(), { signal: controller.signal });
    if (!response.ok) {
      var mensagem = "Não foi possível pesquisar no banco de dados.";
      if (response.status === 400) mensagem = (await response.json()).error;
      throw new Error(mensagem);
    }
    var payload = await response.json();
    if (controller.signal.aborted) return;
    QUESTIONS_DATA = payload.questions.map(montarQuestao);
    currentPage = payload.page;
    exibirResultados(QUESTIONS_DATA, payload.total);
  } catch (error) {
    if (controller.signal.aborted) return;
    exibirResultados([], 0);
    count.textContent = error instanceof TypeError ? "Não foi possível conectar ao servidor." : error.message;
  }
}

function exibirResultados(resultados, total) {
  var metadataDialog = document.getElementById("mobileMetadata");
  if (metadataDialog.open && !resultados.some(function (q) { return q.id === currentSelectedId; })) {
    metadataDialog.close();
  }
  updateActiveFilterPills();
  document.getElementById("totalQuestionsCount").textContent =
    "Exibindo " + resultados.length + " de " + total + " questões cadastradas";
  renderQuestionCards(resultados);
  var totalPages = Math.ceil(total / itemsPerPage) || 1;
  renderPagination(totalPages);
  document.getElementById("metaActionBtn").disabled = resultados.length === 0;
  if (resultados.length > 0) {
    var aindaVisivel = resultados.find(function (q) { return q.id === currentSelectedId; });
    selectQuestion(aindaVisivel ? currentSelectedId : resultados[0].id);
  } else {
    currentSelectedId = null;
  }
}

function renderQuestionCards(items) {
  var container = document.getElementById("questionsList");
  if (!container) return;

  if (items.length === 0) {
    container.innerHTML =
      '<div class="panel-card-custom p-8 text-center space-y-3">' +
      '<i class="fa-solid fa-folder-open text-3xl text-gray-500"></i>' +
      '<h3 class="text-base font-semibold text-gray-200">Nenhuma questão encontrada</h3>' +
      '<p class="text-xs text-gray-400">Tente ajustar os termos da busca ou remover alguns filtros laterais.</p>' +
      "</div>";
    return;
  }

  var themeStyles = ["blue", "green", "red"];
  var themeIcons = [
    "fa-solid fa-book-open", "fa-solid fa-scroll", "fa-solid fa-landmark",
    "fa-solid fa-feather-pointed", "fa-solid fa-box-archive"
  ];

  container.innerHTML = items.map(function (q, idx) {
    var isSelected = q.id === currentSelectedId ? "active-selected" : "";
    var theme = themeStyles[idx % themeStyles.length];
    var iconClass = themeIcons[idx % themeIcons.length];
    var tagsHTML = q.tags.slice(0, 4).map(function (t) {
      return '<span class="bg-black/30 text-gray-200 text-[10px] font-semibold px-2 py-0.5 rounded border border-white/10 mr-1">' +
        esc(t) + "</span>";
    }).join("");

    return '<article class="card-custom ' + theme + " " + isSelected +
      '" onclick="selectQuestion(' + q.id + ')" data-id="' + q.id + '">' +
      '<button type="button" class="mobile-only mobile-question" aria-haspopup="dialog" aria-controls="mobileMetadata" ' +
      'onclick="event.stopPropagation(); selectQuestion(' + q.id + ', this)">' +
      '<span class="mobile-question-context">' + esc(q.edicaoAno || "—") + " · " + esc(q.phase) + "</span>" +
      "<strong>" + esc((q.category === "Tarefa" ? "Tarefa " : "Questão ") + q.num) + "</strong>" +
      '<span class="mobile-question-preview">' + esc(q.descShort) + "</span></button>" +
      '<div class="card-header">' +
      '<span class="text-[10px] font-bold text-gray-200 uppercase bg-black/40 px-2.5 py-1 rounded border border-white/10 backdrop-blur-sm">' +
      "ONHB · " + esc(q.edicaoAno || "—") + " · " + esc(q.phase) + "</span>" +
      '<span class="text-[11px] text-gray-300 font-medium">' +
      '<i class="fa-regular fa-clock mr-1"></i> ' + esc(q.category) + "</span>" +
      "</div>" +
      '<div class="card-body">' +
      '<div class="icon"><i class="' + iconClass + '"></i></div>' +
      "<div>" +
      "<h3>" + esc(q.headline) + "</h3>" +
      "<p>" + esc(q.descShort) + "</p>" +
      '<div class="progress"><div>' +
      "<span>" + esc(q.period || "Período não informado") +
      (q.historicYear ? " · " + q.historicYear : "") + "</span>" +
      '<div class="progress-bar"></div></div></div>' +
      '<div class="flex flex-wrap items-center justify-between gap-2 mt-4 pt-2 border-t border-white/10">' +
      '<div class="flex flex-wrap items-center gap-1">' + tagsHTML + "</div>" +
      "<div>" +
      '<a href="javascript:void(0)" onclick="event.stopPropagation(); openQuestionModal(' + q.id + ')" class="btn-first">' +
      'Ver completa <i class="fa-solid fa-arrow-right text-[10px] ml-1"></i></a>' +
      '<a href="javascript:void(0)" onclick="event.stopPropagation(); selectQuestion(' + q.id + ')" class="btn-second">Inspetor</a>' +
      "</div></div></div></div></article>";
  }).join("");
}

function selectQuestion(id, mobileTrigger) {
  currentSelectedId = id;
  var q = encontrarQuestao(id);
  if (!q) return;

  document.querySelectorAll(".card-custom").forEach(function (card) {
    if (parseInt(card.getAttribute("data-id"), 10) === id) card.classList.add("active-selected");
    else card.classList.remove("active-selected");
  });

  var metaImg = document.getElementById("metaImage");
  metaImg.classList.toggle("hidden", !q.img);
  if (q.img) metaImg.src = q.img;
  else metaImg.removeAttribute("src");

  document.getElementById("metaTitle").textContent = q.title || "--";
  document.getElementById("metaAuthor").textContent = q.author;
  document.getElementById("metaDate").textContent = q.date;
  document.getElementById("metaType").textContent = q.type;
  document.getElementById("metaPeriod").textContent = q.period || "--";
  document.getElementById("metaOrigin").textContent = q.origin;
  document.getElementById("metaLang").textContent = q.language;
  document.getElementById("metaEditionBadge").textContent = "ONHB " + (q.edicaoAno || "");

  var tagsContainer = document.getElementById("metaTagsContainer");
  tagsContainer.innerHTML = q.tags.length
    ? q.tags.map(function (t) {
        return '<span class="bg-teal-950 text-teal-300 border border-teal-700/50 px-2 py-0.5 rounded text-[10px] font-semibold">' +
          esc(t) + "</span>";
      }).join("")
    : '<span class="text-gray-500 text-[10px] italic">Sem tags</span>';
  if (mobileTrigger && mobileLayout.matches) {
    mobileQuestionTrigger = mobileTrigger;
    mobileTrigger.focus({ preventScroll: true });
    openMobilePanel("mobileMetadata", "metadataPanel");
  }
}

function renderPagination(totalPages) {
  var pContainer = document.getElementById("paginationBar");
  if (!pContainer) return;
  if (totalPages <= 1) { pContainer.innerHTML = ""; return; }

  function janela() {
    var paginas = [];
    var inicio = Math.max(1, currentPage - 2);
    var fim = Math.min(totalPages, inicio + 4);
    inicio = Math.max(1, fim - 4);
    for (var i = inicio; i <= fim; i++) paginas.push(i);
    return paginas;
  }

  var buttons = [];
  buttons.push('<button onclick="changePage(' + (currentPage - 1) + ')" ' +
    (currentPage === 1 ? "disabled" : "") +
    ' class="w-8 h-8 rounded-lg border border-gray-600 bg-[#181916] hover:bg-gray-700 text-gray-300 flex items-center justify-center transition-colors shadow-sm disabled:opacity-40 cursor-pointer"><i class="fa-solid fa-chevron-left text-[10px]"></i></button>');

  janela().forEach(function (i) {
    var active = i === currentPage
      ? "bg-[#134e5e] text-white font-bold"
      : "border border-gray-600 bg-[#181916] hover:bg-gray-700 text-gray-300";
    buttons.push('<button onclick="changePage(' + i + ')" class="w-8 h-8 rounded-lg ' +
      active + ' flex items-center justify-center shadow-sm cursor-pointer text-xs">' + i + "</button>");
  });

  buttons.push('<button onclick="changePage(' + (currentPage + 1) + ')" ' +
    (currentPage === totalPages ? "disabled" : "") +
    ' class="w-8 h-8 rounded-lg border border-gray-600 bg-[#181916] hover:bg-gray-700 text-gray-300 flex items-center justify-center transition-colors shadow-sm disabled:opacity-40 cursor-pointer"><i class="fa-solid fa-chevron-right text-[10px]"></i></button>');

  pContainer.innerHTML = buttons.join("");
}

function changePage(page) {
  if (page < 1) return;
  currentPage = page;
  pesquisarNoBanco();
  document.getElementById("repositorio").scrollIntoView({ behavior: "smooth" });
}

function updateActiveFilterPills() {
  var container = document.getElementById("activePillsContainer");
  var badge = document.getElementById("activeFiltersBadge");
  if (!container) return;

  var pills = [];
  if (filterState.searchQuery) pills.push('Busca: "' + filterState.searchQuery + '"');
  if (filterState.fase !== "all") pills.push(filterState.fase);
  if (filterState.periodo !== "all") pills.push(periodoLabel(filterState.periodo));
  if (filterState.edicao !== "all") pills.push("ONHB " + filterState.edicao);
  if (filterState.yearStart || filterState.yearEnd) {
    pills.push((filterState.yearStart || 1500) + " - " + (filterState.yearEnd || 2026));
  }
  filterState.tags.forEach(function (t) { pills.push(t); });

  if (badge) badge.textContent = pills.length + " ativos";

  if (pills.length === 0) {
    container.innerHTML =
      '<span class="text-gray-400 text-[11px] font-medium mr-1">Filtros aplicados:</span> ' +
      '<span class="text-gray-500 text-[11px] italic">Nenhum filtro ativo no momento</span>';
  } else {
    var pillsHTML = pills.map(function (p) {
      return '<span class="inline-flex items-center gap-1 bg-teal-950 text-teal-200 font-medium px-2 py-0.5 rounded border border-teal-700/50 text-[11px]">' +
        esc(p) + "</span>";
    }).join("");
    container.innerHTML =
      '<span class="text-gray-400 text-[11px] font-medium mr-1">Filtros aplicados:</span> ' + pillsHTML;
  }
}

/* ================= Modal ================= */

function openQuestionModal(id) {
  var q = encontrarQuestao(id);
  if (!q) return;
  modalSelectedId = id;

  document.getElementById("modalEdition").textContent = "ONHB · " + q.edition + " · " + q.phase;
  document.getElementById("modalHeadline").textContent = q.headline;
  document.getElementById("modalDesc").textContent = q.desc;
  document.getElementById("modalProvenance").textContent = [
    q.prova ? "Prova: " + q.prova.split("/").pop() + " · página " + q.pagina + " do PDF" : "",
    q.gabarito ? "Gabarito: " + q.gabarito.split("/").pop() :
      q.itens.some(function (it) { return it.pontuacao != null; }) ? "Pontuação do cadastro anterior, sem conferência com o gabarito." : ""
  ].filter(Boolean).join(" | ");
  document.getElementById("modalAuthor").textContent = q.author;
  document.getElementById("modalDate").textContent = q.date;
  document.getElementById("modalType").textContent = q.type;
  document.getElementById("modalOrigin").textContent = q.origin;
  renderDocumentos(q.documentos);

  var wrap = document.getElementById("modalAlternativesWrapper");
  var alt = document.getElementById("modalAlternatives");
  if (wrap && alt) {
    if (q.itens.length) {
      wrap.classList.remove("hidden");
      alt.innerHTML = q.itens.map(function (it) {
        return '<div class="bg-[#161714] p-3 rounded-lg border border-gray-800">' +
          '<div class="flex justify-between text-[11px] text-teal-300 font-semibold mb-1">' +
          "<span>" + esc(it.identificador || "Item") + "</span>" +
          "<span>" + esc(rotuloPontuacao(it.pontuacao)) + "</span></div>" +
          '<p class="text-gray-300 text-xs leading-relaxed">' + esc(it.texto || "") + "</p></div>";
      }).join("");
    } else {
      wrap.classList.toggle("hidden", q.category !== "Objetiva");
      alt.textContent = q.category === "Objetiva" ? "Alternativas e pontuação ainda não cadastradas." : "";
    }
  }

  var metadataDialog = document.getElementById("mobileMetadata");
  var openedFromMobile = metadataDialog.open;
  if (openedFromMobile) metadataDialog.close();
  document.getElementById("questionModal").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  if (openedFromMobile) document.getElementById("questionModalClose").focus();
}

function rotuloPontuacao(valor) {
  return valor == null ? "Pontuação não informada" : valor + (valor === 1 ? " ponto" : " pontos");
}

function renderDocumentos(documentos) {
  var container = document.getElementById("modalDocuments");
  container.replaceChildren();
  if (!documentos.length) container.textContent = "Nenhum documento cadastrado para esta questão.";
  documentos.forEach(function (doc) {
    var details = document.createElement("details");
    details.className = "bg-[#161714] p-3 rounded-lg border border-gray-800";
    var title = document.createElement("summary");
    title.className = "cursor-pointer text-teal-300";
    title.textContent = doc.nome + (doc.tipo ? " · " + doc.tipo : "");
    details.append(title);
    details.addEventListener("toggle", function () {
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "true";
      (doc.imagens || []).forEach(function (src) {
        if (!/^\/api\/imagens\/[a-f0-9]{64}$/.test(src)) return;
        var link = document.createElement("a");
        link.href = src;
        link.target = "_blank";
        link.rel = "noopener";
        var img = document.createElement("img");
        img.src = src;
        img.alt = doc.nome;
        img.className = "w-full max-h-96 object-contain rounded mt-3";
        link.append(img);
        details.append(link);
      });
      (doc.urls || []).forEach(function (url) {
        if (!/^https?:\/\//i.test(url)) return;
        var link = document.createElement("a");
        link.href = url;
        link.className = "block break-all text-teal-300 underline mt-2";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = url;
        details.append(link);
      });
      var text = document.createElement("p");
      text.className = "mt-3 whitespace-pre-wrap text-gray-300";
      details.append(text);
      if (doc.tem_texto) {
        fetch("/api/documentos/" + encodeURIComponent(doc.id)).then(function (response) {
          if (!response.ok) throw new Error();
          return response.json();
        }).then(function (data) {
          text.textContent = data.texto;
        }).catch(function () { text.textContent = "Não foi possível carregar o texto da fonte."; });
      } else if (!(doc.imagens || []).length && !(doc.urls || []).length) {
        text.textContent = "Documento catalogado, sem arquivo ou link cadastrado.";
      }
    });
    container.append(details);
  });
}

function closeQuestionModal() {
  document.getElementById("questionModal").classList.add("hidden");
  document.body.style.overflow = "";
  modalSelectedId = null;
  if (mobileLayout.matches && mobileQuestionTrigger && mobileQuestionTrigger.isConnected) {
    mobileQuestionTrigger.focus({ preventScroll: true });
  }
}

function downloadTranscription() {
  var q = encontrarQuestao(modalSelectedId == null ? currentSelectedId : modalSelectedId);
  if (!q) return;
  var linhas = [
    "ONHB - " + q.edition + " - " + q.phase,
    "Questão " + q.num + " (" + q.category + ")",
    "Período: " + (q.period || "não informado"),
    "Datação: " + q.date,
    "Tipo: " + q.type,
    "Acervo: " + q.origin,
    "Tags: " + (q.tags.join(", ") || "nenhuma"),
    "",
    "ENUNCIADO:",
    q.desc,
    ""
  ];
  if (q.itens.length) {
    linhas.push("ALTERNATIVAS / ITENS:");
    q.itens.forEach(function (it) {
      linhas.push("[" + (it.identificador || "") + "] (" + rotuloPontuacao(it.pontuacao) + ") " + (it.texto || ""));
    });
  } else {
    linhas.push("Nenhuma alternativa cadastrada.");
  }

  var blob = new Blob([linhas.join("\n")], { type: "text/plain;charset=utf-8" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "questao-" + q.num + "-onhb.txt";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showNotification("Transcrição da questão baixada!");
}

function showNotification(msg) {
  var toast = document.getElementById("toastNotification");
  var toastMsg = document.getElementById("toastMsg");
  if (!toast || !toastMsg) return;
  toastMsg.textContent = msg;
  toast.classList.remove("hidden");
  toast.classList.add("flex");
  setTimeout(function () {
    toast.classList.add("hidden");
    toast.classList.remove("flex");
  }, 3200);
}

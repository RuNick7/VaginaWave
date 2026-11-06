// Первый вход, как в Яндекс Музыке: «Кого вы любите?» — артисты кружками, рядом с отмеченным
// появляются похожие по звучанию; потом «Как вам слушается?» — характер волны и настроение.
import { get, post } from "./api.js";
import { cover, h, icon, toast } from "./dom.js";
import { chips, empty, group, loading, toggle, WAVE_LABELS } from "./components.js";

const ENOUGH = 3; // как в Яндексе: хотя бы три артиста

const HINTS = {
  favorite: "Больше того, что вы уже любите и часто дослушиваете.",
  balanced: "Любимое вперемешку с новым, похожим на ваш вкус.",
  discover: "В основном треки, которые вы ещё не слышали.",
  forgotten: "Отмеченное и любимое, что давно не звучало.",
};

export function setupOnboarding(player, box) {
  let artists = [];
  let selected = new Set();
  let settings = {};
  let found = null;
  let timer = null;
  let saving = false;

  function close() {
    box.hidden = true;
    box.replaceChildren();
  }

  async function open(data) {
    box.hidden = false;
    box.replaceChildren(h("div", { class: "onboarding__loading" }, loading()));
    try {
      const d = data || await get("/api/onboarding");
      artists = d.artists;
      selected = new Set(d.artists.filter((a) => a.liked).map((a) => a.id));
      settings = d.settings || {};
      found = null;
      showArtists();
    } catch (e) {
      box.replaceChildren(empty(e.message), h("button", { class: "btn", type: "button", onClick: close }, "Закрыть"));
    }
  }

  function showArtists() {
    const grid = h("div", { class: "bubbles" });
    const input = h("input", { class: "search__input", type: "search", placeholder: "Найти артиста", "aria-label": "Найти артиста", autocomplete: "off" });
    const next = h("button", { class: "btn", type: "button", onClick: () => {
      if (selected.size >= ENOUGH) showPreferences();
      else toast("Отметьте хотя бы трёх артистов");
    } });

    const renderNext = () => {
      const n = selected.size;
      next.textContent = n >= ENOUGH ? `Дальше · ${n}` : `Ещё ${ENOUGH - n}`;
      next.classList.toggle("btn--primary", n >= ENOUGH);
    };
    const renderGrid = () => {
      grid.replaceChildren(...(found ?? artists).map(bubble));
      renderNext();
    };

    function bubble(artist) {
      const on = selected.has(artist.id);
      return h("button", { class: `bubble${on ? " is-on" : ""}`, type: "button", "aria-pressed": String(on), onClick: () => choose(artist) },
        h("span", { class: "bubble__img" },
          cover(artist.cover, "cover--round"),
          on ? h("span", { class: "bubble__check" }, icon("check")) : null,
        ),
        h("span", { class: "bubble__name" }, artist.title),
      );
    }

    async function choose(artist) {
      if (selected.has(artist.id)) {
        selected.delete(artist.id);
        return renderGrid();
      }
      selected.add(artist.id);
      if (!artists.some((a) => a.id === artist.id)) artists = [artist, ...artists];
      renderGrid();
      // похожие встают сразу за выбранным: новые и те, что были ниже по списку
      const similar = await get(`/api/onboarding/similar/${encodeURIComponent(artist.id)}`).then((r) => r.artists).catch(() => []);
      const bring = similar.filter((s) => !selected.has(s.id));
      if (!bring.length) return;
      const ids = new Set(bring.map((s) => s.id));
      const rest = artists.filter((a) => !ids.has(a.id));
      rest.splice(rest.findIndex((a) => a.id === artist.id) + 1, 0, ...bring);
      artists = rest;
      renderGrid();
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = input.value.trim();
        found = q ? await get(`/api/search?q=${encodeURIComponent(q)}`).then((r) => r.artists).catch(() => null) : null;
        renderGrid();
      }, 250);
    });

    box.replaceChildren(
      h("div", { class: "onboarding__head" },
        h("h1", { class: "onboarding__title" }, "Кого вы любите?"),
        h("p", { class: "hint" }, "Отметьте хотя бы трёх — первая волна начнётся с них. Похожие появятся рядом."),
        h("label", { class: "search" }, icon("search"), input),
      ),
      h("div", { class: "onboarding__scroll" }, grid),
      h("div", { class: "onboarding__foot" },
        h("button", { class: "link-btn", type: "button", onClick: () => finish([]) }, "Пропустить"),
        next,
      ),
    );
    renderGrid();
  }

  function showPreferences() {
    const hint = h("p", { class: "hint" }, HINTS[settings.character] || "");
    const done = h("button", { class: "btn btn--primary", type: "button", onClick: () => finish([...selected]) }, "Готово");
    box.replaceChildren(
      h("div", { class: "onboarding__scroll onboarding__prefs" },
        h("h1", { class: "onboarding__title" }, "Как вам слушается?"),
        h("p", { class: "hint" }, "Это можно поменять в любой момент кнопкой под волной."),
        group("Что чаще ставить",
          chips(WAVE_LABELS.character, settings.character || "balanced", (v) => {
            settings = { ...settings, character: v };
            hint.textContent = HINTS[v] || "";
          }),
          hint,
        ),
        group("Настроение", chips(WAVE_LABELS.mood, settings.mood || "any", (v) => { settings = { ...settings, mood: v }; })),
        group(null, toggle("Без слов", settings.no_vocals, (v) => { settings = { ...settings, no_vocals: v }; })),
      ),
      h("div", { class: "onboarding__foot" },
        h("button", { class: "link-btn", type: "button", onClick: showArtists }, "Назад"),
        done,
      ),
    );
  }

  async function finish(chosen) {
    if (saving) return;
    saving = true;
    try {
      await post("/api/onboarding", { artists: chosen, settings });
      player.settings = (await get("/api/settings")).settings;
      player.emit();
      if (chosen.length) toast("Волна начнёт с ваших артистов");
      close();
    } catch (e) {
      toast(e.message);
    } finally {
      saving = false;
    }
  }

  return { open };
}

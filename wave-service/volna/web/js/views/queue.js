// Очередь, как в Spotify: что играет, что добавлено вручную (можно перетаскивать за «≡», убирать
// и запускать с любого места) и что пойдёт потом — волна или остаток альбома.
import { h, icon } from "../dom.js";
import { empty, group, pageHeader, trackRow } from "../components.js";

export function renderQueue(root, { player }) {
  const body = h("div", {});
  root.append(pageHeader("Очередь", { back: true }), body);
  let dragging = false;

  function render() {
    if (dragging) return;
    if (!player.track) {
      body.replaceChildren(empty("Сейчас ничего не играет. Добавьте трек через меню «⋯» или смахните его вправо — он встанет в очередь."));
      return;
    }
    const rows = player.queue.map((track, i) =>
      h("div", { class: "queue-row" },
        trackRow(track, { player, queueable: false, onPlay: () => player.playFromQueue(i) }),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Убрать из очереди", onClick: () => player.removeFromQueue(i) }, icon("close")),
        grip(i),
      ),
    );
    const list = h("div", { class: "queue-list" }, ...rows);
    const nodes = [
      group("Сейчас играет", trackRow(player.track, { player, queueable: false, onPlay: () => player.toggle() })),
      h("div", { class: "group" },
        h("div", { class: "queue-head" },
          h("div", { class: "group__label" }, "В очереди"),
          player.queue.length ? h("button", { class: "link-btn", type: "button", onClick: () => player.clearQueue() }, "Очистить") : null,
        ),
        player.queue.length ? list : h("p", { class: "hint" }, "Смахните трек вправо в любом списке или выберите «В очередь» в меню «⋯». Очередь играет раньше волны."),
      ),
      player.upNext.length
        ? group(player.source ? `Дальше: ${player.source}` : "Дальше",
          ...player.upNext.slice(0, 20).map((track) => trackRow(track, { player, onPlay: () => {} })))
        : null,
    ];
    body.replaceChildren(...nodes.filter(Boolean));
  }

  /** Ручка: потянуть вверх или вниз — трек меняется местами с соседями. */
  function grip(start) {
    const handle = h("span", { class: "queue-grip", role: "button", "aria-label": "Перетащить" }, icon("grip"));
    handle.addEventListener("pointerdown", (down) => {
      down.preventDefault();
      handle.setPointerCapture(down.pointerId);
      const row = handle.parentElement;
      const height = row.offsetHeight;
      let index = start;
      let origin = down.clientY;
      dragging = true;
      row.classList.add("is-lifted");
      const move = (e) => {
        let offset = e.clientY - origin;
        if (offset > height / 2 && row.nextElementSibling) {
          row.parentElement.insertBefore(row.nextElementSibling, row);
          player.moveInQueue(index, index + 1);
          index += 1;
          origin += height;
          offset -= height;
        } else if (offset < -height / 2 && row.previousElementSibling) {
          row.parentElement.insertBefore(row, row.previousElementSibling);
          player.moveInQueue(index, index - 1);
          index -= 1;
          origin -= height;
          offset += height;
        }
        row.style.transform = `translateY(${offset}px)`;
      };
      const end = () => {
        handle.removeEventListener("pointermove", move);
        dragging = false;
        render();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end, { once: true });
      handle.addEventListener("pointercancel", end, { once: true });
    });
    return handle;
  }

  player.addEventListener("change", render);
  render();
  return () => player.removeEventListener("change", render);
}

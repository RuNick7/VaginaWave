// Жесты пальцем, как в приложении: свайп по обложке и названию листает треки, свайп строки трека
// вправо ставит его в очередь. Вертикальная прокрутка остаётся браузеру, мышью ничего не тянется.

const SKIP_AT = 72;
const QUEUE_AT = 88;

function horizontalDrag(el, { onMove, onEnd }) {
  let drag = null;
  el.style.touchAction = "pan-y";
  el.addEventListener("pointerdown", (e) => {
    drag = e.pointerType === "touch" ? { x: e.clientX, y: e.clientY, id: e.pointerId, active: false, dx: 0 } : null;
  });
  el.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.active) {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      if (Math.abs(dy) > Math.abs(dx)) {
        drag = null; // листают страницу
        return;
      }
      drag.active = true;
      el.setPointerCapture?.(e.pointerId);
    }
    drag.dx = dx;
    onMove(dx);
  });
  const finish = (commit) => (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (drag.active) {
      swallowClick(el);
      onEnd(commit ? drag.dx : 0);
    }
    drag = null;
  };
  el.addEventListener("pointerup", finish(true));
  el.addEventListener("pointercancel", finish(false));
}

/** После свайпа браузер ещё присылает click: он не должен нажать кнопку под пальцем. */
function swallowClick(el) {
  const stop = (e) => {
    e.stopPropagation();
    e.preventDefault();
  };
  el.addEventListener("click", stop, { capture: true, once: true });
  setTimeout(() => el.removeEventListener("click", stop, { capture: true }), 350);
}

/** Влево — следующий трек, вправо — предыдущий. */
export function trackSwipe(el, player) {
  horizontalDrag(el, {
    onMove(dx) {
      if (!player.track) return;
      el.style.transition = "none";
      el.style.transform = `translateX(${dx * 0.5}px)`;
    },
    onEnd(dx) {
      el.style.transition = "transform 0.2s ease";
      el.style.transform = "";
      if (!player.track) return;
      if (dx <= -SKIP_AT) player.next("skip");
      else if (dx >= SKIP_AT) player.previous(false);
    },
  });
}

/** Строка трека: смахнуть вправо — в очередь. */
export function queueSwipe(row, track, player) {
  let armed = false;
  horizontalDrag(row, {
    onMove(dx) {
      const shift = Math.max(0, Math.min(dx, QUEUE_AT * 1.4));
      row.classList.add("is-swiping");
      row.style.setProperty("--swipe", `${shift}px`);
      if (!armed && shift >= QUEUE_AT) navigator.vibrate?.(10);
      armed = shift >= QUEUE_AT;
      row.classList.toggle("is-armed", armed);
    },
    onEnd(dx) {
      row.classList.remove("is-swiping", "is-armed");
      row.style.removeProperty("--swipe");
      armed = false;
      if (dx >= QUEUE_AT) player.enqueue(track);
    },
  });
}

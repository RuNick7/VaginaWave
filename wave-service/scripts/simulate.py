"""Симуляция слушателя на демо-библиотеке: учится ли волна на пропусках и не уплывает ли плейлист.

Запуск:  python scripts/simulate.py
"""

from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from volna.demo import GENRES, DemoWorld  # noqa: E402
from volna.engine import Steer, WaveSession  # noqa: E402
from volna.profile import build_context_profile, build_profile  # noqa: E402
from volna.services import Deps, Vectors  # noqa: E402
from volna.store import Store  # noqa: E402

# Вероятность дослушать трек по жанрам. Джаз (2) слушатель раньше любил, а теперь
# пропускает: волна должна это заметить, хотя в истории Navidrome джаза много.
LISTENER = {0: 0.85, 1: 0.8, 2: 0.1, 3: 0.75, 4: 0.3, 5: 0.5, 6: 0.05, 7: 0.4, -1: 0.35}


def make_deps(seed: int = 7) -> tuple[DemoWorld, Deps]:
    world = DemoWorld(seed=seed)
    store = Store(Path(tempfile.mkdtemp()) / "sim.db")
    return world, Deps(library=world, sim=world, store=store, vectors=Vectors(world, store), demo=True)


def listen(world: DemoWorld, session: WaveSession, tracks: int, rng: np.random.Generator, listener: dict | None = None) -> list[tuple[int, bool]]:
    """Прогоняет tracks треков; возвращает (жанр, дослушал?) для каждого."""
    log = []
    track = session.start()
    for _ in range(tracks):
        if track is None:
            break
        genre = world.genre[track.id]
        done = rng.random() < (listener or LISTENER)[genre]
        if done:
            track = session.advance(track.id, "complete", track.duration, track.duration)
        else:
            track = session.advance(track.id, "skip", float(rng.uniform(3, 20)), track.duration)
        log.append((genre, done))
    return log


def wave_learning(sessions: int = 6, per_session: int = 25, seed: int = 1, listener: dict | None = None) -> list[dict]:
    world, deps = make_deps()
    rng = np.random.default_rng(seed)
    report = []
    for n in range(sessions):
        profile = build_profile(deps)  # между сессиями профиль пересобирается с учётом пропусков
        log = listen(world, WaveSession(deps, profile, "balanced", seed=seed + n), per_session, rng, listener)
        genres = Counter(g for g, _ in log)
        after_skip = [log[i + 1][0] == g for i, (g, done) in enumerate(log[:-1]) if not done]
        report.append(
            {
                "session": n + 1,
                "completion": sum(done for _, done in log) / len(log),
                "jazz_share": genres[2] / len(log),
                "same_after_skip": sum(after_skip) / len(after_skip) if after_skip else 0.0,
                "top": ", ".join(f"{GENRES[g] if g >= 0 else 'кроссовер'} {c}" for g, c in genres.most_common(3)),
            }
        )
    return report


def continuation_drift(length: int = 40, seed: int = 3) -> dict:
    """Плейлист из двух жанров: продолжение должно держать оба, а не только жанр последней песни."""
    world, deps = make_deps()
    rng = np.random.default_rng(seed)
    a = [i for i in world.ids if world.genre[i] == 0]
    b = [i for i in world.ids if world.genre[i] == 7]
    playlist = [x for pair in zip(rng.choice(a, 8, replace=False), rng.choice(b, 8, replace=False)) for x in pair]
    playlist = [str(i) for i in playlist]

    taste = build_profile(deps)
    context = build_context_profile(deps, playlist, taste)
    steer = Steer(context, "list", title="Пост-рок + Неоклассика")
    session = WaveSession(deps, taste, "balanced", steer=steer, exclude=playlist, seed=seed)
    ours = []
    track = session.start()
    while track is not None and len(ours) < length:
        ours.append(world.genre[track.id])
        track = session.advance(track.id, "complete", track.duration, track.duration)

    # Типичное «радио по последней песне»: случайный из 5 ближайших к предыдущему треку
    naive, played, last = [], set(playlist), playlist[-1]
    while len(naive) < length:
        options = [c.id for c in world.similar(last, 30) if c.id not in played][:5]
        last = str(rng.choice(options))
        played.add(last)
        naive.append(world.genre[last])

    def summary(genres: list[int]) -> dict:
        in_playlist = [g in (0, 7) for g in genres]
        count = Counter(genres)
        return {
            "in_playlist": sum(in_playlist) / len(genres),
            "balance": min(count[0], count[7]) / max(count[0], count[7], 1),
            "last_10_in_playlist": sum(in_playlist[-10:]) / 10,
        }

    return {"volna": summary(ours), "last_song_radio": summary(naive)}


def settings_effect(n: int = 25, seeds: int = 3) -> list[dict]:
    """Как настройки волны меняют звучание и долю нового."""
    from volna.settings import WaveSettings, track_traits

    cases = [
        ("Без настроек", WaveSettings()),
        ("Настроение: спокойное", WaveSettings(mood="calm")),
        ("Настроение: бодрое", WaveSettings(mood="energetic")),
        ("Занятие: засыпаю", WaveSettings(activity="sleep")),
        ("Без слов", WaveSettings(no_vocals=True)),
        ("Характер: любимое", WaveSettings(character="favorite")),
        ("Характер: незнакомое", WaveSettings(character="discover")),
    ]
    rows = []
    for label, settings in cases:
        energy, instrumental, new = [], [], []
        for seed in range(seeds):
            world, deps = make_deps()
            session = WaveSession(deps, build_profile(deps), settings, seed=seed)
            track = session.start()
            for _ in range(n):
                traits = track_traits(world.features_map[track.id])
                energy.append(traits.get("energy", 0.5))
                instrumental.append(traits.get("instrumental", 0.5))
                new.append(not (track.play_count > 0 or track.starred))
                track = session.advance(track.id, "complete", track.duration, track.duration)
        rows.append({"label": label, "energy": np.mean(energy), "instrumental": np.mean(instrumental), "new": np.mean(new)})
    return rows


def wave_by_track(seeds: int = 3, n: int = 25) -> list[dict]:
    """Волна по треку из жанра, которого нет во вкусе (Неоклассика): как сила смещения меняет выдачу."""
    from volna.settings import WaveSettings

    rows = []
    for strength in (0.35, 0.65, 0.9):
        same, liked = [], []
        for seed in range(seeds):
            world, deps = make_deps()
            taste = build_profile(deps)
            tid = [i for i in world.ids if world.genre[i] == 7][5 + seed]
            steer = Steer(build_context_profile(deps, [tid], taste), "track", tid, "трек")
            session = WaveSession(deps, taste, WaveSettings(steer=strength), steer=steer, exclude=[tid], seed=seed)
            track = session.start()
            for _ in range(n):
                same.append(world.genre[track.id] == 7)
                liked.append(world.genre[track.id] in (0, 1, 3))
                track = session.advance(track.id, "complete", track.duration, track.duration)
        rows.append({"strength": strength, "same": np.mean(same), "liked": np.mean(liked)})
    return rows


def moody_listener(seeds: int = 8, sessions: int = 30, per_session: int = 30) -> dict:
    """Слушатель с настроением: любит несколько жанров, но сейчас хочет два из них (в половине сессий —
    те же, что в прошлый раз). 60 % пропусков — пролистывание в первую секунду. Четыре пропуска
    подряд — закрывает приложение. Так выглядит реальная история, на которую жаловались."""
    import time

    from volna.engine import warm_start

    base = {0: .85, 1: .8, 2: .1, 3: .75, 4: .3, 5: .5, 6: .05, 7: .4, -1: .35}
    done, after_two, first_five, abandoned, total = [], [], [], 0, 0
    for seed in range(seeds):
        world, deps = make_deps()
        rng = np.random.default_rng(seed)
        mood = None
        for n in range(sessions):
            profile = build_profile(deps)
            if mood is None or rng.random() < .5:
                mood = set(rng.choice([0, 1, 3, 5, 7], size=2, replace=False).tolist())
            p = {g: (min(.95, v + .15) if g in mood else v * .4) for g, v in base.items()}
            now = time.time()
            session = WaveSession(deps, profile, "balanced", seed=seed * 1000 + n,
                                  warm=warm_start(deps.store.events(since=now - 30 * 86400), now))
            track, log = session.start(), []
            while track is not None and len(log) < per_session:
                ok = rng.random() < p[world.genre[track.id]]
                if len(log) >= 2 and not log[-1] and not log[-2]:
                    after_two.append(ok)
                if len(log) < 5:
                    first_five.append(ok)
                position = track.duration if ok else (1.0 if rng.random() < .6 else float(rng.uniform(8, 25)))
                track = session.advance(track.id, "complete" if ok else "skip", position, track.duration)
                log.append(ok)
                if len(log) >= 4 and not any(log[-4:]):
                    abandoned += 1
                    break
            done += log
            total += 1
    return {"completion": np.mean(done), "after_two_skips": np.mean(after_two), "first_five": np.mean(first_five),
            "abandoned": abandoned / total}


def main() -> None:
    print("1. Волна учится на пропусках (слушатель разлюбил джаз)\n")
    print(f"{'сессия':>6} {'дослушано':>10} {'доля джаза':>11} {'тот же жанр после скипа':>24}  что играло чаще всего")
    for row in wave_learning():
        print(
            f"{row['session']:>6} {row['completion']:>10.0%} {row['jazz_share']:>11.0%}"
            f" {row['same_after_skip']:>24.0%}  {row['top']}"
        )

    print("\n2. Продолжение плейлиста «Пост-рок + Неоклассика», 40 треков\n")
    drift = continuation_drift()
    print(f"{'':<22}{'в жанрах плейлиста':>20}{'баланс жанров':>15}{'последние 10':>14}")
    for name, label in (("volna", "Volna (смещение)"), ("last_song_radio", "Радио по последней")):
        s = drift[name]
        print(f"{label:<22}{s['in_playlist']:>20.0%}{s['balance']:>15.0%}{s['last_10_in_playlist']:>14.0%}")

    print("\n3. Моя волна по треку из Неоклассики (её нет во вкусе слушателя)\n")
    print(f"{'смещение':>9}{'похожее на трек':>17}{'из любимых жанров':>19}")
    for row in wave_by_track():
        print(f"{row['strength']:>9.2f}{row['same']:>17.0%}{row['liked']:>19.0%}")

    print("\n4. Настройки волны (энергия и «инструментальность» по шкале 0–1)\n")
    print(f"{'':<24}{'энергия':>9}{'без слов':>10}{'новое':>8}")
    for row in settings_effect():
        print(f"{row['label']:<24}{row['energy']:>9.2f}{row['instrumental']:>10.2f}{row['new']:>8.0%}")

    print("\n5. Слушатель с настроением: пролистывает, после серии пропусков закрывает приложение\n")
    m = moody_listener()
    print(f"дослушано {m['completion']:.0%}, после двух пропусков заходит {m['after_two_skips']:.0%}, "
          f"из первых пяти — {m['first_five']:.0%}, брошенных сессий {m['abandoned']:.0%}")


if __name__ == "__main__":
    main()

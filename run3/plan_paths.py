#!/usr/bin/env python3
"""
Планировщик двух маршрутов по matrix.json для пары дронов Сверх.Рой.

Матрица 5×5:
  1 (и любое значение кроме -1) — клетка ArUco, по ней можно летать и искать цвета;
 -1 — клетка деления: на ней лежит цветной лист, в путь не входит.

Скрипт сам находит, вдоль или поперёк лежит деление, режет поле на две
непересекающиеся зоны, назначает каждому дрону ближайшую зону и строит
покрытие «змейкой». Если оба дрона стоят в одной зоне, один сначала
проходит в свою через разрыв в делении.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from typing import Iterable, List, Optional, Sequence, Set, Tuple


Cell = Tuple[int, int]
GRID = 5
NOFLY = -1
STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def in_grid(x: int, y: int, h: int, w: int) -> bool:
    return 0 <= x < w and 0 <= y < h


def neighbors(x: int, y: int, h: int, w: int) -> Iterable[Cell]:
    for dx, dy in STEPS:
        nx, ny = x + dx, y + dy
        if in_grid(nx, ny, h, w):
            yield nx, ny


def is_flyable(matrix: Sequence[Sequence[int]], x: int, y: int) -> bool:
    return matrix[y][x] != NOFLY


def flyable_cells(matrix: Sequence[Sequence[int]]) -> Set[Cell]:
    h, w = len(matrix), len(matrix[0])
    return {(x, y) for y in range(h) for x in range(w) if is_flyable(matrix, x, y)}


def nofly_cells(matrix: Sequence[Sequence[int]]) -> List[Cell]:
    h, w = len(matrix), len(matrix[0])
    return [(x, y) for y in range(h) for x in range(w) if matrix[y][x] == NOFLY]


def snap_to_flyable(cell: Cell, walkable: Set[Cell]) -> Cell:
    if cell in walkable:
        return cell
    if not walkable:
        return cell
    cx, cy = cell
    return min(walkable, key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))


def bfs_path(start: Cell, goal: Cell, walkable: Set[Cell]) -> List[Cell]:
    if start == goal:
        return [start]
    if start not in walkable or goal not in walkable:
        return []
    prev = {start: None}
    q = deque([start])
    h = max(p[1] for p in walkable) + 1
    w = max(p[0] for p in walkable) + 1
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for nxt in neighbors(cur[0], cur[1], h, w):
            if nxt in walkable and nxt not in prev:
                prev[nxt] = cur
                q.append(nxt)
    if goal not in prev:
        return []
    path: List[Cell] = []
    cur: Optional[Cell] = goal
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return path


def bfs_dist(start: Cell, goals: Set[Cell], walkable: Set[Cell]) -> int:
    if start in goals:
        return 0
    if start not in walkable or not goals:
        return 10**6
    seen = {start}
    q = deque([(start, 0)])
    h = max(p[1] for p in walkable) + 1
    w = max(p[0] for p in walkable) + 1
    while q:
        cur, dist = q.popleft()
        for nxt in neighbors(cur[0], cur[1], h, w):
            if nxt not in walkable or nxt in seen:
                continue
            if nxt in goals:
                return dist + 1
            seen.add(nxt)
            q.append((nxt, dist + 1))
    return 10**6


def detect_split(matrix: Sequence[Sequence[int]]) -> dict:
    """Ось деления: 'x' — вертикальная стена (лево/право), 'y' — горизонтальная (низ/верх)."""
    blocked = nofly_cells(matrix)
    h, w = len(matrix), len(matrix[0])
    if not blocked:
        return {
            "axis": "x",
            "coord": w // 2,
            "kind": "vertical",
            "blocked": [],
            "fallback": True,
        }

    xs = [x for x, _ in blocked]
    ys = [y for _, y in blocked]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    if y_span >= x_span:
        coord = int(round(sum(xs) / len(xs)))
        return {
            "axis": "x",
            "coord": coord,
            "kind": "vertical",
            "blocked": blocked,
            "fallback": False,
        }
    coord = int(round(sum(ys) / len(ys)))
    return {
        "axis": "y",
        "coord": coord,
        "kind": "horizontal",
        "blocked": blocked,
        "fallback": False,
    }


def split_regions(matrix: Sequence[Sequence[int]], split: dict) -> dict:
    h, w = len(matrix), len(matrix[0])
    axis, coord = split["axis"], split["coord"]
    side_a: Set[Cell] = set()
    side_b: Set[Cell] = set()
    gaps: Set[Cell] = set()

    for y in range(h):
        for x in range(w):
            if not is_flyable(matrix, x, y):
                continue
            value = x if axis == "x" else y
            cell = (x, y)
            if value < coord:
                side_a.add(cell)
            elif value > coord:
                side_b.add(cell)
            else:
                gaps.add(cell)

    return {
        "a": side_a,
        "b": side_b,
        "gaps": gaps,
        "label_a": "left" if axis == "x" else "top",
        "label_b": "right" if axis == "x" else "bottom",
    }


def assign_drones(
    drone1: Cell,
    drone2: Cell,
    region_a: Set[Cell],
    region_b: Set[Cell],
    walkable: Set[Cell],
) -> dict:
    cost1a = bfs_dist(drone1, region_a, walkable)
    cost1b = bfs_dist(drone1, region_b, walkable)
    cost2a = bfs_dist(drone2, region_a, walkable)
    cost2b = bfs_dist(drone2, region_b, walkable)

    keep = cost1a + cost2b
    swap = cost1b + cost2a
    if keep <= swap:
        return {
            "drone1_region": "a",
            "drone2_region": "b",
            "cost": keep,
            "costs": {"d1_a": cost1a, "d1_b": cost1b, "d2_a": cost2a, "d2_b": cost2b},
        }
    return {
        "drone1_region": "b",
        "drone2_region": "a",
        "cost": swap,
        "costs": {"d1_a": cost1a, "d1_b": cost1b, "d2_a": cost2a, "d2_b": cost2b},
    }


def extend_unique(path: List[Cell], extra: Sequence[Cell]) -> None:
    for cell in extra:
        if not path or path[-1] != cell:
            path.append(cell)


def _group_keys(groups: dict, start_key: int) -> List[int]:
    keys = sorted(groups)
    if abs(start_key - keys[0]) > abs(start_key - keys[-1]):
        keys.reverse()
    return keys


def snake_coverage(cells: Set[Cell], start: Cell, axis: str,
                   corridor: Optional[Set[Cell]] = None) -> List[Cell]:
    """Змейка: целые колонки (деление вдоль) или ряды (поперёк), без прыжков через поле."""
    if not cells:
        return []
    start = start if start in cells else min(
        cells, key=lambda p: abs(p[0] - start[0]) + abs(p[1] - start[1])
    )
    walkable = set(cells) | (corridor or set())
    path: List[Cell] = []
    current = start

    if axis == "x":
        groups = {}
        for x, y in cells:
            groups.setdefault(x, []).append(y)
        keys = _group_keys(groups, start[0])
        first = sorted(groups[keys[0]])
        reverse = abs(start[1] - first[-1]) < abs(start[1] - first[0])
        for key in keys:
            ys = sorted(groups[key], reverse=reverse)
            first_cell = (key, ys[0])
            conn = bfs_path(current, first_cell, walkable) or [current, first_cell]
            extend_unique(path, conn)
            for y in ys:
                extend_unique(path, [(key, y)])
            current = path[-1]
            reverse = not reverse
        return path

    groups = {}
    for x, y in cells:
        groups.setdefault(y, []).append(x)
    keys = _group_keys(groups, start[1])
    first = sorted(groups[keys[0]])
    reverse = abs(start[0] - first[-1]) < abs(start[0] - first[0])
    for key in keys:
        xs = sorted(groups[key], reverse=reverse)
        first_cell = (xs[0], key)
        conn = bfs_path(current, first_cell, walkable) or [current, first_cell]
        extend_unique(path, conn)
        for x in xs:
            extend_unique(path, [(x, key)])
        current = path[-1]
        reverse = not reverse
    return path


def connect_through(start: Cell, goal: Cell, walkable: Set[Cell]) -> List[Cell]:
    path = bfs_path(start, goal, walkable)
    return path if path else [start, goal]


def pad_for_transit(route1: List[Cell], transit_len1: int,
                    route2: List[Cell], transit_len2: int) -> Tuple[List[Cell], List[Cell]]:
    """Пока один дрон переходит в свою зону, второй ждёт на старте — траектории не пересекаются."""
    wait = max(transit_len1, transit_len2)
    extra1 = wait - transit_len1
    extra2 = wait - transit_len2
    if extra1 > 0 and route1:
        route1 = [route1[0]] * extra1 + route1
    if extra2 > 0 and route2:
        route2 = [route2[0]] * extra2 + route2
    return route1, route2


def plan_mission(matrix: Sequence[Sequence[int]],
                 drone1: Sequence[int],
                 drone2: Sequence[int]) -> dict:
    h, w = len(matrix), len(matrix[0])
    if h != GRID or w != GRID:
        raise ValueError(f"Ожидается матрица {GRID}x{GRID}, получено {h}x{w}")

    walkable = flyable_cells(matrix)
    d1 = snap_to_flyable((int(drone1[0]), int(drone1[1])), walkable)
    d2 = snap_to_flyable((int(drone2[0]), int(drone2[1])), walkable)

    split = detect_split(matrix)
    regions = split_regions(matrix, split)
    region_a, region_b, gaps = regions["a"], regions["b"], regions["gaps"]

    if not region_a and gaps:
        region_a = set(gaps)
        gaps = set()
    if not region_b and gaps:
        region_b = set(gaps)
        gaps = set()

    assignment = assign_drones(d1, d2, region_a, region_b, walkable)

    def region_of(which: str) -> Set[Cell]:
        return region_a if which == "a" else region_b

    def label_of(which: str) -> str:
        return regions["label_a"] if which == "a" else regions["label_b"]

    r1 = region_of(assignment["drone1_region"])
    r2 = region_of(assignment["drone2_region"])

    def build_route(start: Cell, region: Set[Cell]) -> Tuple[List[Cell], List[Cell]]:
        if not region:
            return [start], []
        entry = start if start in region else min(
            region, key=lambda p: bfs_dist(start, {p}, walkable)
        )
        transit: List[Cell] = []
        if start not in region:
            # Идём через любые летабельные клетки, включая разрыв деления.
            transit = connect_through(start, entry, walkable)
        coverage = snake_coverage(region, entry, split["axis"], corridor=gaps)
        route: List[Cell] = []
        extend_unique(route, transit if transit else [start])
        extend_unique(route, coverage)
        transit_cells = transit[:-1] if len(transit) > 1 else []
        return route, transit_cells

    route1, transit1 = build_route(d1, r1)
    route2, transit2 = build_route(d2, r2)
    route1, route2 = pad_for_transit(route1, len(transit1), route2, len(transit2))

    return {
        "split": {
            "axis": split["axis"],
            "coord": split["coord"],
            "kind": split["kind"],
            "fallback": split["fallback"],
            "nofly": [list(c) for c in split["blocked"]],
        },
        "labels": {
            "a": regions["label_a"],
            "b": regions["label_b"],
        },
        "drone1": list(d1),
        "drone2": list(d2),
        "assignment": {
            "drone1": label_of(assignment["drone1_region"]),
            "drone2": label_of(assignment["drone2_region"]),
        },
        "region1": sorted(r1),
        "region2": sorted(r2),
        "gaps": sorted(gaps),
        "transit1": transit1,
        "transit2": transit2,
        "route1": route1,
        "route2": route2,
    }


def load_matrix_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "matrix" not in data:
        raise ValueError("В JSON нет поля matrix")
    data.setdefault("drone1", [0, 0])
    data.setdefault("drone2", [4, 0])
    return data


def ascii_plan(matrix: Sequence[Sequence[int]], plan: dict) -> str:
    h, w = len(matrix), len(matrix[0])
    r1 = set(plan["region1"])
    r2 = set(plan["region2"])
    d1 = tuple(plan["drone1"])
    d2 = tuple(plan["drone2"])
    lines = [
        f"деление: {plan['split']['kind']} @ {plan['split']['axis']}={plan['split']['coord']}",
        f"дрон1 -> {plan['assignment']['drone1']}  старт {d1}",
        f"дрон2 -> {plan['assignment']['drone2']}  старт {d2}",
        f"разрыв деления (коридор): {plan['gaps']}",
        "",
        "карта:  A=зона1  B=зона2  #=деление  1/2=старт  +=коридор",
    ]
    for y in range(h):
        row = []
        for x in range(w):
            cell = (x, y)
            if matrix[y][x] == NOFLY:
                ch = "#"
            elif cell == d1 and cell == d2:
                ch = "X"
            elif cell == d1:
                ch = "1"
            elif cell == d2:
                ch = "2"
            elif cell in r1:
                ch = "A"
            elif cell in r2:
                ch = "B"
            else:
                ch = "+"
            row.append(ch)
        lines.append(f"y={y}  " + " ".join(row))
    lines.append("     " + " ".join(str(x) for x in range(w)))
    lines.append("")
    lines.append("маршрут 1: " + " -> ".join(f"({x},{y})" for x, y in plan["route1"]))
    lines.append("маршрут 2: " + " -> ".join(f"({x},{y})" for x, y in plan["route2"]))
    return "\n".join(lines)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _path_is_adjacent(route: Sequence[Cell]) -> bool:
    for a, b in zip(route, route[1:]):
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) > 1:
            return False
    return True


def self_check() -> None:
    sample = [
        [0, 0, -1, 1, 1],
        [0, 0,  0, 1, 1],
        [0, 0,  0, 1, 1],
        [0, 0,  1, 1, 1],
        [0, 0, -1, 1, 1],
    ]
    plan = plan_mission(sample, [0, 0], [4, 0])
    _assert(plan["split"]["kind"] == "vertical", "sample: вертикальное деление")
    _assert(plan["assignment"]["drone1"] == "left", "sample: дрон1 слева")
    _assert(plan["assignment"]["drone2"] == "right", "sample: дрон2 справа")
    _assert(not plan["transit1"] and not plan["transit2"], "sample: транзит не нужен")
    _assert((2, 0) not in plan["route1"] and (2, 0) not in plan["route2"], "sample: не летаем на -1")
    _assert((2, 4) not in plan["route1"] and (2, 4) not in plan["route2"], "sample: не летаем на -1")
    _assert(_path_is_adjacent(plan["route1"]) and _path_is_adjacent(plan["route2"]), "sample: шаг на соседнюю клетку")
    covered = set(plan["region1"]) | set(plan["region2"])
    _assert(not (set(plan["region1"]) & set(plan["region2"])), "зоны не пересекаются")
    _assert(covered == flyable_cells(sample) - set(plan["gaps"]), "покрыты обе половины")

    same_side = plan_mission(sample, [0, 0], [1, 0])
    _assert(same_side["assignment"]["drone1"] != same_side["assignment"]["drone2"],
            "оба слева: разные зоны")
    going_right = same_side["transit1"] or same_side["transit2"]
    _assert(going_right, "оба слева: один идёт направо через разрыв")
    trans = same_side["transit2"] or same_side["transit1"]
    _assert(all(sample[y][x] != NOFLY for x, y in trans), "транзит только по летабельным")
    _assert(_path_is_adjacent(same_side["route1"]) and _path_is_adjacent(same_side["route2"]),
            "оба слева: шаг на соседнюю клетку")

    swapped = plan_mission(sample, [4, 0], [0, 0])
    _assert(swapped["assignment"]["drone1"] == "right", "обмен местами: дрон1 справа")
    _assert(swapped["assignment"]["drone2"] == "left", "обмен местами: дрон2 слева")

    website = [
        [1, 1, -1, 1, 1],
        [1, 1,  1, 1, 1],
        [1, 1,  1, 1, 1],
        [1, 1,  1, 1, 1],
        [1, 1, -1, 1, 1],
    ]
    web = plan_mission(website, [0, 0], [4, 0])
    _assert(web["split"]["kind"] == "vertical", "сайт: вертикальное деление")
    _assert(web["assignment"]["drone1"] == "left" and web["assignment"]["drone2"] == "right",
            "сайт: каждый в своей половине")

    horizontal = [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [-1, 1, 1, 1, -1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
    ]
    hor = plan_mission(horizontal, [0, 0], [4, 4])
    _assert(hor["split"]["kind"] == "horizontal", "поперёк: горизонтальное деление")
    _assert(hor["assignment"]["drone1"] == "top", "поперёк: дрон1 в верхней зоне (y=0)")
    _assert(hor["assignment"]["drone2"] == "bottom", "поперёк: дрон2 в нижней зоне (y=4)")

    both_top_start = plan_mission(horizontal, [0, 0], [1, 0])
    _assert(both_top_start["assignment"]["drone1"] != both_top_start["assignment"]["drone2"],
            "оба сверху: разные зоны")
    _assert(both_top_start["transit1"] or both_top_start["transit2"],
            "оба сверху: один идёт вниз")
    _assert(_path_is_adjacent(hor["route1"]) and _path_is_adjacent(hor["route2"]),
            "поперёк: шаг на соседнюю клетку")
    _assert(_path_is_adjacent(both_top_start["route1"]) and _path_is_adjacent(both_top_start["route2"]),
            "оба сверху: шаг на соседнюю клетку")

    print("self_check: ok")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Построить два маршрута по matrix.json")
    parser.add_argument("json_path", nargs="?", help="Путь к matrix.json")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        self_check()
        if not args.json_path:
            return 0

    if not args.json_path:
        parser.print_help()
        return 1

    data = load_matrix_json(args.json_path)
    plan = plan_mission(data["matrix"], data["drone1"], data["drone2"])
    print(ascii_plan(data["matrix"], plan))
    print()
    print(json.dumps({
        "assignment": plan["assignment"],
        "split": plan["split"],
        "route1": [list(p) for p in plan["route1"]],
        "route2": [list(p) for p in plan["route2"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

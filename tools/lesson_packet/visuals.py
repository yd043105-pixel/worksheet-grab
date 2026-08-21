"""Private source crops and declarative, grayscale-safe vector scenes."""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image as PillowImage
from reportlab.graphics.shapes import Circle, Drawing, Ellipse, Line, PolyLine, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Image


MAX_VISUAL_WIDTH = 154 * mm
MAX_VISUAL_HEIGHT = 110 * mm
GRAY_TOKENS = {
    "ink": HexColor("#1D1D1D"),
    "muted": HexColor("#777777"),
    "light": HexColor("#DADADA"),
    "paper": white,
    "none": None,
}
PRIMITIVE_KINDS = {
    "line", "polyline", "path", "rect", "rounded_rect", "circle", "ellipse",
    "text", "arrow", "dimension", "axis", "marker", "connector", "group", "repeat",
}
STYLE_FIELDS = {"stroke", "fill", "strokeWidth", "dash", "fontSize", "textAnchor"}
ARROW_HEAD_SIZE = 5.0
AXIS_TICK_SIZE = 4.0
AXIS_TICK_LABEL_GAP = 2.0
PRESSURE_SYMBOLS = {
    "gas_pressure": "P_gas",
    "atmospheric_pressure": "P_atm",
}
PRESSURE_FACTOR_SYMBOLS = {
    "density": "rho",
    "gravity": "g",
    "height": "h",
}


def _normalized_crop(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("crop must contain four normalized bounds")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError("crop bounds must be normalized numbers")
    left, top, right, bottom = (float(item) for item in value)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("crop bounds must be normalized and ordered")
    return left, top, right, bottom


def _page_image_path(visual: dict, root: Path) -> Path:
    page = visual.get("sourcePage")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("sourcePage must be a one-based integer")
    filename = f"page-{page:03d}.png"
    candidates = [root / filename]
    if isinstance(visual.get("sourceStem"), str) and visual["sourceStem"].strip():
        candidates.insert(0, root / visual["sourceStem"] / filename)
    if isinstance(visual.get("sourceFile"), str) and visual["sourceFile"].strip():
        candidates.append(root / Path(visual["sourceFile"]).stem / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"rendered source page not found: {filename}")


def source_crop_flowable(visual: dict, page_image_root: Path | str) -> Image:
    """Return a ReportLab flowable cropped from normalized source-page bounds."""
    left, top, right, bottom = _normalized_crop(visual.get("crop"))
    with PillowImage.open(_page_image_path(visual, Path(page_image_root))) as source:
        width, height = source.size
        crop_pixels = (round(left * width), round(top * height), round(right * width), round(bottom * height))
        cropped = source.crop(crop_pixels)
        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
    buffer.seek(0)
    flowable = Image(buffer)
    scale = min(MAX_VISUAL_WIDTH / flowable.imageWidth, MAX_VISUAL_HEIGHT / flowable.imageHeight)
    flowable.drawWidth = flowable.imageWidth * scale
    flowable.drawHeight = flowable.imageHeight * scale
    flowable._lesson_crop_buffer = buffer
    flowable._lesson_crop_pixels = crop_pixels
    return flowable


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _number(value: Any, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise ValueError(f"{name} is out of range")
    return result


def _known_fields(value: dict, allowed: set[str], name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{name} has unknown field: {sorted(unknown)[0]}")


def _style(value: Any) -> dict:
    if value is None:
        return {"stroke": "ink", "fill": "none", "strokeWidth": 1, "dash": "solid", "fontSize": 8, "textAnchor": "start"}
    if not isinstance(value, dict):
        raise ValueError("style must be an object")
    _known_fields(value, STYLE_FIELDS, "style")
    style = {"stroke": "ink", "fill": "none", "strokeWidth": 1, "dash": "solid", "fontSize": 8, "textAnchor": "start"}
    style.update(value)
    for field in ("stroke", "fill"):
        if not isinstance(style[field], str) or style[field] not in GRAY_TOKENS:
            raise ValueError(f"style {field} must use a validated grayscale token")
    style["strokeWidth"] = _number(style["strokeWidth"], "style strokeWidth", 0.25, 5)
    if not isinstance(style["dash"], str) or style["dash"] not in {"solid", "dashed", "dotted"}:
        raise ValueError("style dash must be solid, dashed, or dotted")
    style["fontSize"] = _number(style["fontSize"], "style fontSize", 6, 18)
    if not isinstance(style["textAnchor"], str) or style["textAnchor"] not in {"start", "middle", "end"}:
        raise ValueError("style textAnchor is invalid")
    return style


def _dash(style: dict) -> list[int] | None:
    return {"solid": None, "dashed": [4, 2], "dotted": [1, 2]}[style["dash"]]


def _point(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be a normalized [x, y] pair")
    return _number(value[0], f"{name} x", 0, 1), _number(value[1], f"{name} y", 0, 1)


def _primitive_ids(primitives: list[dict], known_ids: set[str] | None = None) -> set[str]:
    ids = set() if known_ids is None else known_ids
    for primitive in primitives:
        if not isinstance(primitive, dict):
            raise ValueError("primitive must be an object")
        kind = primitive.get("kind")
        if not isinstance(kind, str) or kind not in PRIMITIVE_KINDS:
            raise ValueError("primitive kind is unsupported")
        semantic_id = primitive.get("semanticId")
        if semantic_id is not None:
            semantic_id = _text(semantic_id, "primitive semanticId")
            if semantic_id in ids:
                raise ValueError(f"duplicate semantic id: {semantic_id}")
            ids.add(semantic_id)
        if kind == "group":
            _primitive_ids(primitive.get("primitives", []), ids)
        elif kind == "repeat" and isinstance(primitive.get("primitive"), dict):
            _primitive_ids([primitive["primitive"]], ids)
    return ids


def _validate_primitive(primitive: dict, *, nested: bool = False) -> None:
    if not isinstance(primitive, dict):
        raise ValueError("primitive must be an object")
    kind = primitive.get("kind")
    if not isinstance(kind, str) or kind not in PRIMITIVE_KINDS:
        raise ValueError("primitive kind is unsupported")
    common = {"kind", "semanticId", "style"}
    specific = {
        "line": {"x1", "y1", "x2", "y2"},
        "connector": {"x1", "y1", "x2", "y2", "label"},
        "arrow": {"x1", "y1", "x2", "y2", "label"},
        "dimension": {"x1", "y1", "x2", "y2", "label"},
        "axis": {"x1", "y1", "x2", "y2", "label", "ticks"},
        "polyline": {"points", "closed"},
        "path": {"points", "closed"},
        "rect": {"x", "y", "width", "height"},
        "rounded_rect": {"x", "y", "width", "height", "radius"},
        "circle": {"cx", "cy", "r"},
        "ellipse": {"cx", "cy", "rx", "ry"},
        "text": {"x", "y", "text"},
        "marker": {"x", "y", "marker", "label", "size"},
        "group": {"transform", "primitives"},
        "repeat": {"count", "translate", "primitive"},
    }
    _known_fields(primitive, common | specific[kind], "primitive")
    if "style" in primitive and not isinstance(primitive["style"], dict):
        raise ValueError("style must be an object")
    _style(primitive.get("style"))
    if kind in {"line", "connector", "arrow", "dimension", "axis"}:
        for field in ("x1", "y1", "x2", "y2"):
            _number(primitive.get(field), f"primitive {field}", 0, 1)
        if kind in {"arrow", "dimension", "axis"}:
            _text(primitive.get("label"), f"{kind} label")
            if primitive["x1"] == primitive["x2"] and primitive["y1"] == primitive["y2"]:
                raise ValueError(f"{kind} endpoints must differ")
        elif kind == "connector" and "label" in primitive:
            _text(primitive["label"], "connector label")
        if kind == "axis" and "ticks" in primitive:
            ticks = primitive["ticks"]
            if not isinstance(ticks, list):
                raise ValueError("axis ticks must be a list")
            for tick in ticks:
                if not isinstance(tick, dict):
                    raise ValueError("axis tick must be an object")
                _known_fields(tick, {"position", "label"}, "axis tick")
                _number(tick.get("position"), "axis tick position", 0, 1)
                if "label" in tick:
                    _text(tick["label"], "axis tick label")
    elif kind in {"polyline", "path"}:
        points = primitive.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError(f"{kind} requires at least two points")
        for point in points:
            _point(point, f"{kind} point")
        if "closed" in primitive and not isinstance(primitive["closed"], bool):
            raise ValueError(f"{kind} closed must be boolean")
    elif kind in {"rect", "rounded_rect"}:
        for field in ("x", "y", "width", "height"):
            _number(primitive.get(field), f"{kind} {field}", 0, 1)
        if primitive["x"] + primitive["width"] > 1 or primitive["y"] + primitive["height"] > 1:
            raise ValueError(f"{kind} exceeds normalized canvas")
        if kind == "rounded_rect":
            _number(primitive.get("radius"), "rounded_rect radius", 0, min(primitive["width"], primitive["height"]) / 2)
    elif kind in {"circle", "ellipse"}:
        fields = ("cx", "cy", "r") if kind == "circle" else ("cx", "cy", "rx", "ry")
        for field in fields:
            _number(primitive.get(field), f"{kind} {field}", 0, 1)
    elif kind == "text":
        _number(primitive.get("x"), "text x", 0, 1)
        _number(primitive.get("y"), "text y", 0, 1)
        _text(primitive.get("text"), "text")
    elif kind == "marker":
        _number(primitive.get("x"), "marker x", 0, 1)
        _number(primitive.get("y"), "marker y", 0, 1)
        marker = primitive.get("marker", "dot")
        if not isinstance(marker, str) or marker not in {"dot", "cross", "x"}:
            raise ValueError("marker is unsupported")
        if "label" in primitive:
            _text(primitive["label"], "marker label")
        if "size" in primitive:
            _number(primitive["size"], "marker size", 1, 20)
    elif kind == "group":
        transform = primitive.get("transform", {})
        if not isinstance(transform, dict):
            raise ValueError("group transform must be an object")
        _known_fields(transform, {"translate", "scale"}, "group transform")
        if "translate" in transform:
            _point(transform["translate"], "group translate")
        if "scale" in transform:
            scale = transform["scale"]
            if isinstance(scale, list):
                _point(scale, "group scale")
            else:
                _number(scale, "group scale", 0, 1)
        children = primitive.get("primitives")
        if not isinstance(children, list) or not children:
            raise ValueError("group requires primitives")
        for child in children:
            _validate_primitive(child, nested=True)
    else:
        count = primitive.get("count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("repeat count must be an integer")
        _number(count, "repeat count", 1, 100)
        _point(primitive.get("translate"), "repeat translate")
        child = primitive.get("primitive")
        if not isinstance(child, dict):
            raise ValueError("repeat requires a primitive")
        _validate_primitive(child, nested=True)


def _text_bounds(text: str, x: float, y: float, style: dict) -> tuple[float, float, float, float]:
    width = stringWidth(text, "Helvetica", style["fontSize"])
    if style["textAnchor"] == "middle":
        left = x - width / 2
    elif style["textAnchor"] == "end":
        left = x - width
    else:
        left = x
    return left, y - style["fontSize"] * 0.25, left + width, y + style["fontSize"]


def _axis_tick_geometry(
    primitive: dict, tick: dict, point,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    x1, y1 = point(primitive["x1"], primitive["y1"])
    x2, y2 = point(primitive["x2"], primitive["y2"])
    position = tick["position"]
    center_x = x1 + (x2 - x1) * position
    center_y = y1 + (y2 - y1) * position
    length = math.hypot(x2 - x1, y2 - y1)
    normal_x, normal_y = -(y2 - y1) / length, (x2 - x1) / length
    start = (center_x - normal_x * AXIS_TICK_SIZE, center_y - normal_y * AXIS_TICK_SIZE)
    end = (center_x + normal_x * AXIS_TICK_SIZE, center_y + normal_y * AXIS_TICK_SIZE)
    label = (
        center_x - normal_x * (AXIS_TICK_SIZE + AXIS_TICK_LABEL_GAP),
        center_y - normal_y * (AXIS_TICK_SIZE + AXIS_TICK_LABEL_GAP),
    )
    return start, end, label


def _validate_transformed_bounds(
    primitive: dict,
    width: float,
    height: float,
    transform: tuple[float, float, float, float] = (1, 1, 0, 0),
) -> None:
    """Reject any rendered stroke, marker, arrowhead, or label outside the canvas."""
    sx, sy, tx, ty = transform
    style = _style(primitive.get("style"))
    stroke_pad = style["strokeWidth"] / 2 if style["stroke"] != "none" else 0

    def point(x: float, y: float) -> tuple[float, float]:
        return (tx + sx * x) * width, (ty + sy * y) * height

    def inside(bounds: tuple[float, float, float, float]) -> None:
        left, bottom, right, top = bounds
        if left < 0 or bottom < 0 or right > width or top > height:
            raise ValueError("primitive visible bounds exceed canvas")

    def points_bounds(points: list[tuple[float, float]], pad: float = stroke_pad) -> None:
        xs, ys = zip(*points)
        inside((min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad))

    kind = primitive["kind"]
    if kind in {"line", "connector", "arrow", "dimension", "axis"}:
        first = point(primitive["x1"], primitive["y1"])
        second = point(primitive["x2"], primitive["y2"])
        pad = stroke_pad
        if kind in {"arrow", "dimension", "axis"}:
            pad += ARROW_HEAD_SIZE
        points_bounds([first, second], pad)
        if kind in {"arrow", "dimension", "axis"} or "label" in primitive:
            inside(_text_bounds(
                primitive["label"],
                (first[0] + second[0]) / 2,
                (first[1] + second[1]) / 2 + ARROW_HEAD_SIZE,
                style,
            ))
        if kind == "axis":
            for tick in primitive.get("ticks", []):
                tick_start, tick_end, label_point = _axis_tick_geometry(primitive, tick, point)
                points_bounds([tick_start, tick_end])
                if "label" in tick:
                    tick_style = {**style, "textAnchor": "middle"}
                    inside(_text_bounds(tick["label"], *label_point, tick_style))
    elif kind in {"polyline", "path"}:
        points_bounds([point(*item) for item in primitive["points"]])
    elif kind in {"rect", "rounded_rect"}:
        first = point(primitive["x"], primitive["y"])
        second = point(primitive["x"] + primitive["width"], primitive["y"] + primitive["height"])
        points_bounds([first, second])
    elif kind in {"circle", "ellipse"}:
        center_x, center_y = point(primitive["cx"], primitive["cy"])
        if kind == "circle":
            base_radius = primitive["r"] * min(width, height)
            radius_x, radius_y = base_radius * sx, base_radius * sy
        else:
            radius_x = primitive["rx"] * sx * width
            radius_y = primitive["ry"] * sy * height
        inside((
            center_x - radius_x - stroke_pad,
            center_y - radius_y - stroke_pad,
            center_x + radius_x + stroke_pad,
            center_y + radius_y + stroke_pad,
        ))
    elif kind == "text":
        inside(_text_bounds(primitive["text"], *point(primitive["x"], primitive["y"]), style))
    elif kind == "marker":
        x, y = point(primitive["x"], primitive["y"])
        size = primitive.get("size", 4)
        radius = size / 2 if primitive.get("marker", "dot") == "dot" else size
        inside((x - radius - stroke_pad, y - radius - stroke_pad, x + radius + stroke_pad, y + radius + stroke_pad))
        if "label" in primitive:
            inside(_text_bounds(primitive["label"], x + size + 3, y + size + 2, style))
    elif kind == "group":
        group = primitive.get("transform", {})
        translate = group.get("translate", [0, 0])
        scale = group.get("scale", 1)
        scale_x, scale_y = (scale if isinstance(scale, list) else (scale, scale))
        child_transform = (sx * scale_x, sy * scale_y, tx + sx * translate[0], ty + sy * translate[1])
        for child in primitive["primitives"]:
            _validate_transformed_bounds(child, width, height, child_transform)
    else:
        dx, dy = primitive["translate"]
        for index in range(primitive["count"]):
            child_transform = (sx, sy, tx + sx * dx * index, ty + sy * dy * index)
            _validate_transformed_bounds(primitive["primitive"], width, height, child_transform)


def _validate_semantics(visual: dict, primitive_ids: set[str]) -> None:
    semantic_ids = set(primitive_ids)

    def register(value: Any, name: str) -> str:
        semantic_id = _text(value, name)
        if semantic_id in semantic_ids:
            raise ValueError(f"duplicate semantic id: {semantic_id}")
        semantic_ids.add(semantic_id)
        return semantic_id

    def references(value: Any, name: str, known_ids: set[str]) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{name} must be a non-empty list")
        result = []
        for reference in value:
            reference = _text(reference, f"{name} reference")
            if reference not in known_ids:
                raise ValueError(f"{name} references an unknown semantic id")
            result.append(reference)
        return result

    entities = visual.get("entities")
    if not isinstance(entities, list) or not entities:
        raise ValueError("entities must be a non-empty list")
    entity_ids = set()
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("entity must be an object")
        _known_fields(entity, {"id", "label"}, "entity")
        entity_id = register(entity.get("id"), "entity id")
        entity_ids.add(entity_id)
        if "label" in entity:
            _text(entity["label"], "entity label")
    relationships = visual.get("relationships")
    if not isinstance(relationships, list) or not relationships:
        raise ValueError("relationships must be a non-empty list")
    for relation in relationships:
        if not isinstance(relation, dict):
            raise ValueError("relationship must be an object")
        _known_fields(relation, {"id", "kind", "from", "to", "primitiveIds"}, "relationship")
        register(relation.get("id"), "relationship id")
        _text(relation.get("kind"), "relationship kind")
        source = _text(relation.get("from"), "relationship from")
        target = _text(relation.get("to"), "relationship to")
        if source not in entity_ids or target not in entity_ids:
            raise ValueError("relationship references an unknown entity")
        references(relation.get("primitiveIds"), "relationship primitiveIds", primitive_ids)
    for field in ("invariants", "constraints"):
        values = visual.get(field, [] if field == "constraints" else None)
        if field == "invariants" and (not isinstance(values, list) or not values):
            raise ValueError("invariants must be a non-empty list")
        if not isinstance(values, list):
            raise ValueError(f"{field} must be a list")
        for item in values:
            if not isinstance(item, dict):
                raise ValueError(f"{field[:-1]} must be an object")
            _known_fields(item, {"id", "kind", "refs"}, field[:-1])
            register(item.get("id"), f"{field[:-1]} id")
            _text(item.get("kind"), f"{field[:-1]} kind")
            references(item.get("refs"), field[:-1], primitive_ids | entity_ids)


def _scene_from_schema(schema: dict) -> dict:
    _known_fields(schema, {"scene"}, "schema")
    scene = schema.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("schema requires a scene")
    _known_fields(scene, {"canvas", "primitives"}, "scene")
    canvas = scene.get("canvas")
    if not isinstance(canvas, dict):
        raise ValueError("scene requires a canvas")
    _known_fields(canvas, {"id", "width", "height"}, "canvas")
    _text(canvas.get("id"), "canvas id")
    _number(canvas.get("width"), "canvas width", 10, 1000)
    _number(canvas.get("height"), "canvas height", 10, 1000)
    primitives = scene.get("primitives")
    if not isinstance(primitives, list) or not primitives:
        raise ValueError("scene requires ordered primitives")
    for primitive in primitives:
        _validate_primitive(primitive)
        _validate_transformed_bounds(primitive, canvas["width"], canvas["height"])
    return scene


def _tube_scene(schema: dict) -> dict:
    """Optional adapter: compile structured tube semantics to the generic grammar."""
    _known_fields(schema, {"tube", "labels", "pressureArrows", "pressureRelation"}, "tube schema")
    tube = schema.get("tube")
    if not isinstance(tube, dict):
        raise ValueError("tube schema requires tube")
    _known_fields(tube, {"shape", "sealedSide", "openSide", "liquidLevels"}, "tube")
    shape = tube.get("shape")
    if not isinstance(shape, str) or shape not in {"U", "J"}:
        raise ValueError("tube shape must be U or J")
    sealed, opened = tube.get("sealedSide"), tube.get("openSide")
    if (
        not isinstance(sealed, str) or sealed not in {"left", "right"}
        or not isinstance(opened, str) or opened not in {"left", "right"}
        or sealed == opened
    ):
        raise ValueError("tube requires distinct sealedSide and openSide")
    levels = tube.get("liquidLevels")
    if not isinstance(levels, dict):
        raise ValueError("tube requires liquidLevels")
    left_level = _number(levels.get("left"), "left liquid level", 0, 1)
    right_level = _number(levels.get("right"), "right liquid level", 0, 1)
    if left_level == right_level:
        raise ValueError("tube liquid levels must differ")
    labels = schema.get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError("tube labels must be an object")
    _known_fields(labels, {"sealed", "open", "liquid", "height"}, "tube labels")
    relation = schema.get("pressureRelation")
    if not isinstance(relation, dict):
        raise ValueError("tube requires structured pressureRelation")
    if "expression" in relation:
        raise ValueError("free-form pressure expressions are forbidden")
    _known_fields(relation, {"lhs", "operator", "base", "terms"}, "pressureRelation")
    lhs_key = _text(relation.get("lhs"), "pressure lhs")
    base_key = _text(relation.get("base"), "pressure base")
    if lhs_key not in PRESSURE_SYMBOLS or base_key not in PRESSURE_SYMBOLS:
        raise ValueError("pressure symbols must be allowlisted")
    if lhs_key != "gas_pressure" or base_key != "atmospheric_pressure":
        raise ValueError("pressure relation must use gas_pressure over atmospheric_pressure")
    lhs, operator, base = PRESSURE_SYMBOLS[lhs_key], relation.get("operator"), PRESSURE_SYMBOLS[base_key]
    if operator != "=":
        raise ValueError("pressure relation operator must be =")
    terms = relation.get("terms")
    if not isinstance(terms, list) or len(terms) != 1 or not isinstance(terms[0], dict):
        raise ValueError("pressure relation requires one structured term")
    _known_fields(terms[0], {"operator", "factors"}, "pressure term")
    term_operator = terms[0].get("operator")
    if not isinstance(term_operator, str) or term_operator not in {"+", "-"}:
        raise ValueError("pressure term operator must be + or -")
    factors = terms[0].get("factors")
    expected_factors = ["density", "gravity", "height"]
    if factors != expected_factors or any(
        not isinstance(factor, str) or factor not in PRESSURE_FACTOR_SYMBOLS
        for factor in factors if isinstance(factors, list)
    ):
        raise ValueError("pressure term factors must be allowlisted density, gravity, height")
    term_value = " ".join(PRESSURE_FACTOR_SYMBOLS[factor] for factor in factors)
    expected_sign = "+" if (left_level if sealed == "left" else right_level) < (right_level if opened == "right" else left_level) else "-"
    if term_operator != expected_sign:
        raise ValueError("pressure relation sign conflicts with liquid levels")
    equation = f"{lhs} = {base} {term_operator} {term_value}"
    left_x, right_x, bottom, top = 0.345, 0.655, 0.155, 0.80
    level_y = {"left": bottom + 0.445 * left_level, "right": bottom + 0.445 * right_level}
    primitives = [
        {"kind": "group", "semanticId": "tube", "primitives": [
            {"kind": "line", "x1": left_x, "y1": top, "x2": left_x, "y2": bottom},
            {"kind": "line", "x1": left_x, "y1": bottom, "x2": right_x, "y2": bottom},
            {"kind": "line", "x1": right_x, "y1": bottom, "x2": right_x, "y2": top},
            {"kind": "line", "x1": left_x - 0.03, "y1": level_y["left"], "x2": left_x + 0.03, "y2": level_y["left"], "style": {"strokeWidth": 3}},
            {"kind": "line", "x1": right_x - 0.03, "y1": level_y["right"], "x2": right_x + 0.03, "y2": level_y["right"], "style": {"strokeWidth": 3}},
        ]},
        {"kind": "dimension", "semanticId": "liquid-height", "x1": 0.73, "y1": level_y["left"], "x2": 0.73, "y2": level_y["right"], "label": labels.get("height", "h")},
        {"kind": "text", "semanticId": "pressure-equation", "x": 0.50, "y": 0.93, "text": equation, "style": {"textAnchor": "middle"}},
    ]
    if shape == "J":
        closed_x = left_x if sealed == "left" else right_x
        primitives[0]["primitives"].append({"kind": "line", "x1": closed_x - 0.055, "y1": top, "x2": closed_x + 0.055, "y2": top, "style": {"strokeWidth": 2}})
    else:
        closed_x = left_x if sealed == "left" else right_x
        primitives[0]["primitives"].append({"kind": "line", "x1": closed_x - 0.025, "y1": top, "x2": closed_x + 0.025, "y2": top, "style": {"strokeWidth": 2}})
    label_positions = {"sealed": (left_x if sealed == "left" else right_x, 0.86), "open": (left_x if opened == "left" else right_x, 0.86), "liquid": (0.50, 0.09)}
    for key, (x, y) in label_positions.items():
        if key in labels:
            primitives.append({"kind": "text", "semanticId": f"label-{key}", "x": x, "y": y, "text": labels[key], "style": {"textAnchor": "middle"}})
    arrows = schema.get("pressureArrows", [])
    if not isinstance(arrows, list):
        raise ValueError("pressureArrows must be a list")
    delta = {"up": (0, 0.08), "down": (0, -0.08), "left": (-0.06, 0), "right": (0.06, 0)}
    for index, arrow in enumerate(arrows):
        if not isinstance(arrow, dict):
            raise ValueError("pressure arrow must be an object")
        _known_fields(arrow, {"side", "direction", "label"}, "pressure arrow")
        side, direction = arrow.get("side"), arrow.get("direction")
        if not isinstance(side, str) or side not in {"left", "right"} or not isinstance(direction, str) or direction not in delta:
            raise ValueError("pressure arrow side or direction is invalid")
        start_x = left_x if side == "left" else right_x
        dx, dy = delta[direction]
        primitives.append({"kind": "arrow", "semanticId": f"pressure-arrow-{index}", "x1": start_x, "y1": 0.73, "x2": start_x + dx, "y2": 0.73 + dy, "label": _text(arrow.get("label"), "pressure arrow label")})
    return {"canvas": {"id": "tube-adapter", "width": 420, "height": 225}, "primitives": primitives}


def _drawing(width: float, height: float) -> Drawing:
    drawing = Drawing(width, height)
    drawing._lesson_semantic_labels = []
    drawing._lesson_primitive_kinds = []
    drawing._lesson_semantic_ids = []
    drawing._lesson_axis_tick_count = 0
    return drawing


def _label(drawing: Drawing, text: str, x: float, y: float, style: dict) -> None:
    if text not in drawing._lesson_semantic_labels:
        drawing._lesson_semantic_labels.append(text)
    drawing.add(String(x, y, text, fontName="Helvetica", fontSize=style["fontSize"], fillColor=GRAY_TOKENS[style["stroke"]] or GRAY_TOKENS["ink"], textAnchor=style["textAnchor"]))


def _line(drawing: Drawing, x1: float, y1: float, x2: float, y2: float, style: dict) -> None:
    drawing.add(Line(x1, y1, x2, y2, strokeColor=GRAY_TOKENS[style["stroke"]], strokeWidth=style["strokeWidth"], strokeDashArray=_dash(style)))


def _arrow(drawing: Drawing, x1: float, y1: float, x2: float, y2: float, style: dict, label: str | None = None, both_ends: bool = False) -> None:
    _line(drawing, x1, y1, x2, y2, style)
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        raise ValueError("arrow endpoints must differ")
    def head(x: float, y: float, dx: float, dy: float) -> None:
        size = ARROW_HEAD_SIZE
        ux, uy = dx / length, dy / length
        _line(drawing, x, y, x - size * (ux + uy * 0.45), y - size * (uy - ux * 0.45), style)
        _line(drawing, x, y, x - size * (ux - uy * 0.45), y - size * (uy + ux * 0.45), style)
    head(x2, y2, x2 - x1, y2 - y1)
    if both_ends:
        head(x1, y1, x1 - x2, y1 - y2)
    if label is not None:
        _label(drawing, label, (x1 + x2) / 2, (y1 + y2) / 2 + 5, style)


def _render_primitive(drawing: Drawing, primitive: dict, width: float, height: float, transform: tuple[float, float, float, float] = (1, 1, 0, 0)) -> None:
    kind, style = primitive["kind"], _style(primitive.get("style"))
    sx, sy, tx, ty = transform
    def point(x: float, y: float) -> tuple[float, float]:
        return (tx + sx * x) * width, (ty + sy * y) * height
    semantic_id = primitive.get("semanticId")
    if semantic_id:
        drawing._lesson_semantic_ids.append(semantic_id)
    drawing._lesson_primitive_kinds.append(kind)
    if kind in {"line", "connector"}:
        x1, y1 = point(primitive["x1"], primitive["y1"]); x2, y2 = point(primitive["x2"], primitive["y2"])
        if kind == "connector":
            style = {**style, "dash": "dashed"}
        _line(drawing, x1, y1, x2, y2, style)
        if "label" in primitive:
            _label(drawing, primitive["label"], (x1 + x2) / 2, (y1 + y2) / 2 + 5, style)
    elif kind in {"arrow", "dimension", "axis"}:
        x1, y1 = point(primitive["x1"], primitive["y1"]); x2, y2 = point(primitive["x2"], primitive["y2"])
        _arrow(drawing, x1, y1, x2, y2, style, primitive["label"], both_ends=kind == "dimension")
        if kind == "axis":
            for tick in primitive.get("ticks", []):
                tick_start, tick_end, label_point = _axis_tick_geometry(primitive, tick, point)
                _line(drawing, *tick_start, *tick_end, style)
                drawing._lesson_axis_tick_count += 1
                if "label" in tick:
                    _label(drawing, tick["label"], *label_point, {**style, "textAnchor": "middle"})
    elif kind in {"polyline", "path"}:
        points = [point(*_point(item, "point")) for item in primitive["points"]]
        if primitive.get("closed"):
            points.append(points[0])
        drawing.add(PolyLine(points, strokeColor=GRAY_TOKENS[style["stroke"]], strokeWidth=style["strokeWidth"], strokeDashArray=_dash(style)))
    elif kind in {"rect", "rounded_rect"}:
        x, y = point(primitive["x"], primitive["y"])
        rect_width, rect_height = primitive["width"] * sx * width, primitive["height"] * sy * height
        radius = primitive.get("radius", 0) * min(width, height) * min(sx, sy)
        drawing.add(Rect(x, y, rect_width, rect_height, rx=radius, ry=radius, strokeColor=GRAY_TOKENS[style["stroke"]], fillColor=GRAY_TOKENS[style["fill"]], strokeWidth=style["strokeWidth"], strokeDashArray=_dash(style)))
    elif kind in {"circle", "ellipse"}:
        x, y = point(primitive["cx"], primitive["cy"])
        if kind == "circle":
            base_radius = primitive["r"] * min(width, height)
            rx, ry = base_radius * sx, base_radius * sy
        else:
            rx, ry = primitive["rx"] * sx * width, primitive["ry"] * sy * height
        drawing.add(Ellipse(x, y, rx, ry, strokeColor=GRAY_TOKENS[style["stroke"]], fillColor=GRAY_TOKENS[style["fill"]], strokeWidth=style["strokeWidth"], strokeDashArray=_dash(style)))
    elif kind == "text":
        x, y = point(primitive["x"], primitive["y"])
        _label(drawing, primitive["text"], x, y, style)
    elif kind == "marker":
        x, y = point(primitive["x"], primitive["y"]); size = primitive.get("size", 4)
        marker = primitive.get("marker", "dot")
        if marker == "dot":
            drawing.add(Circle(x, y, size / 2, strokeColor=GRAY_TOKENS[style["stroke"]], fillColor=GRAY_TOKENS[style["stroke"]]))
        else:
            _line(drawing, x - size, y - size, x + size, y + size, style)
            if marker == "cross":
                _line(drawing, x - size, y + size, x + size, y - size, style)
        if "label" in primitive:
            _label(drawing, primitive["label"], x + size + 3, y + size + 2, style)
    elif kind == "group":
        group = primitive.get("transform", {})
        translate = group.get("translate", [0, 0])
        scale = group.get("scale", 1)
        scale_x, scale_y = (scale if isinstance(scale, list) else (scale, scale))
        child_transform = (sx * scale_x, sy * scale_y, tx + sx * translate[0], ty + sy * translate[1])
        for child in primitive["primitives"]:
            _render_primitive(drawing, child, width, height, child_transform)
    else:
        dx, dy = primitive["translate"]
        for index in range(primitive["count"]):
            child_transform = (sx, sy, tx + sx * dx * index, ty + sy * dy * index)
            _render_primitive(drawing, primitive["primitive"], width, height, child_transform)


def _validated_scene(visual: dict, *, require_scene: bool = False) -> dict:
    if not isinstance(visual, dict):
        raise ValueError("visual must be an object")
    schema = visual.get("schema")
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if require_scene and not isinstance(schema.get("scene"), dict):
        raise ValueError("schema requires a scene")
    scene = _scene_from_schema(schema) if "scene" in schema else _scene_from_schema({"scene": _tube_scene(schema)})
    primitive_ids = _primitive_ids(scene["primitives"])
    _validate_semantics(visual, primitive_ids)
    return scene


def validate_reconstructed_visual(visual: dict, *, require_scene: bool = False) -> None:
    """Validate the exact contract consumed by the generic renderer."""
    _validated_scene(visual, require_scene=require_scene)


def reconstructed_visual_flowable(visual: dict) -> Drawing:
    """Render an explicit scene schema; title keywords never select diagram behavior."""
    scene = _validated_scene(visual)
    canvas = scene["canvas"]
    drawing = _drawing(canvas["width"], canvas["height"])
    for primitive in scene["primitives"]:
        _render_primitive(drawing, primitive, drawing.width, drawing.height)
    return drawing

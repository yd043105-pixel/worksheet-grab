"""Source crops and schema-driven vector chemistry visuals."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image as PillowImage
from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.platypus import Image


INK = HexColor("#1D1D1D")
MID_GRAY = HexColor("#777777")
LIGHT_GRAY = HexColor("#DADADA")
MAX_VISUAL_WIDTH = 154 * mm
MAX_VISUAL_HEIGHT = 110 * mm
SUPPORTED_DIAGRAM_TYPES = {
    "apparatus_tube",
    "particle_model",
    "molecular_interactions",
    "cartesian_graph",
    "energy_profile",
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
    source_stem = visual.get("sourceStem")
    if isinstance(source_stem, str) and source_stem.strip():
        candidates.insert(0, root / source_stem / filename)
    source_file = visual.get("sourceFile")
    if isinstance(source_file, str) and source_file.strip():
        candidates.append(root / Path(source_file).stem / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"rendered source page not found: {filename}")


def source_crop_flowable(visual: dict, page_image_root: Path | str) -> Image:
    """Return a ReportLab flowable cropped from normalized source-page bounds."""
    left, top, right, bottom = _normalized_crop(visual.get("crop"))
    page_path = _page_image_path(visual, Path(page_image_root))
    with PillowImage.open(page_path) as page_image:
        width, height = page_image.size
        crop_pixels = (round(left * width), round(top * height), round(right * width), round(bottom * height))
        cropped = page_image.crop(crop_pixels)
        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
    buffer.seek(0)
    flowable = Image(buffer)
    scale = min(MAX_VISUAL_WIDTH / flowable.imageWidth, MAX_VISUAL_HEIGHT / flowable.imageHeight)
    flowable.drawWidth = flowable.imageWidth * scale
    flowable.drawHeight = flowable.imageHeight * scale
    # Keep the stream alive because ReportLab defers decoding until document build.
    flowable._lesson_crop_buffer = buffer
    flowable._lesson_crop_pixels = crop_pixels
    return flowable


def _string_list(visual: dict, field: str) -> list[str]:
    value = visual.get(field)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a non-empty list of strings")
    return value


def _schema(visual: dict) -> dict:
    value = visual.get("schema")
    if not isinstance(value, dict):
        raise ValueError("schema must be an object")
    return value


def _diagram_type(visual: dict) -> str:
    value = visual.get("diagramType")
    if value not in SUPPORTED_DIAGRAM_TYPES:
        raise ValueError("diagramType must explicitly name a supported primitive")
    return value


def _number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise ValueError(f"{label} is out of range")
    return result


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _drawing(width: float, height: float) -> Drawing:
    drawing = Drawing(width, height)
    drawing._lesson_semantic_labels = []
    drawing._lesson_arrow_directions = []
    return drawing


def _label(drawing: Drawing, text: Any, x: float, y: float, *, size: float = 8, anchor: str = "start") -> None:
    label = _text(text, "schema label")
    if label not in drawing._lesson_semantic_labels:
        drawing._lesson_semantic_labels.append(label)
    drawing.add(String(x, y, label, fontName="Helvetica", fontSize=size, fillColor=INK, textAnchor=anchor))


def _arrow(drawing: Drawing, x: float, y: float, direction: str, label: str | None = None) -> None:
    deltas = {"up": (0, 18), "down": (0, -18), "left": (-18, 0), "right": (18, 0)}
    if direction not in deltas:
        raise ValueError("arrow direction must be up, down, left, or right")
    dx, dy = deltas[direction]
    drawing.add(Line(x, y, x + dx, y + dy, strokeColor=INK, strokeWidth=1.2))
    # A short pair of lines creates a copier-safe arrowhead without relying on color.
    if dx:
        drawing.add(Line(x + dx, y + dy, x + dx - (5 if dx > 0 else -5), y + dy + 3, strokeColor=INK))
        drawing.add(Line(x + dx, y + dy, x + dx - (5 if dx > 0 else -5), y + dy - 3, strokeColor=INK))
    else:
        drawing.add(Line(x + dx, y + dy, x + dx + 3, y + dy - (5 if dy > 0 else -5), strokeColor=INK))
        drawing.add(Line(x + dx, y + dy, x + dx - 3, y + dy - (5 if dy > 0 else -5), strokeColor=INK))
    if label is not None:
        _label(drawing, label, x + dx + 4, y + dy + 2)


def _apparatus_tube(schema: dict) -> Drawing:
    tube = schema.get("tube")
    if not isinstance(tube, dict):
        raise ValueError("apparatus_tube schema requires tube")
    shape = tube.get("shape")
    if shape not in {"U", "J"}:
        raise ValueError("tube shape must be U or J")
    sealed_side = tube.get("sealedSide")
    open_side = tube.get("openSide")
    if sealed_side not in {"left", "right"} or open_side not in {"left", "right"} or sealed_side == open_side:
        raise ValueError("tube requires distinct sealedSide and openSide")
    levels = tube.get("liquidLevels")
    if not isinstance(levels, dict):
        raise ValueError("tube requires liquidLevels")
    left_level = _number(levels.get("left"), "left liquid level", 0, 1)
    right_level = _number(levels.get("right"), "right liquid level", 0, 1)
    drawing = _drawing(420, 225)
    left_x, right_x, bottom, top = 145, 275, 35, 180
    drawing.add(Line(left_x, top, left_x, bottom, strokeColor=INK, strokeWidth=2))
    drawing.add(Line(left_x, bottom, right_x, bottom, strokeColor=INK, strokeWidth=2))
    drawing.add(Line(right_x, bottom, right_x, top, strokeColor=INK, strokeWidth=2))
    if shape == "J":
        sealed_x = left_x if sealed_side == "left" else right_x
        closure_end = sealed_x + 24 if sealed_side == "left" else sealed_x - 24
        drawing.add(Line(min(sealed_x, closure_end), top, max(sealed_x, closure_end), top, strokeColor=INK, strokeWidth=2))
    else:
        sealed_x = left_x if sealed_side == "left" else right_x
        drawing.add(Line(sealed_x - 10, top, sealed_x + 10, top, strokeColor=INK, strokeWidth=2))
    level_y = {"left": bottom + 100 * left_level, "right": bottom + 100 * right_level}
    for side, x in (("left", left_x), ("right", right_x)):
        drawing.add(Line(x - 12, level_y[side], x + 12, level_y[side], strokeColor=INK, strokeWidth=4))
    labels = schema.get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError("apparatus labels must be an object")
    for key, x, y in (("sealed", left_x if sealed_side == "left" else right_x, top + 12), ("open", left_x if open_side == "left" else right_x, top + 12), ("liquid", (left_x + right_x) / 2, bottom - 15)):
        if key in labels:
            _label(drawing, labels[key], x, y, anchor="middle")
    height_x = right_x + 30
    drawing.add(Line(height_x, level_y["left"], height_x, level_y["right"], strokeColor=MID_GRAY, strokeDashArray=[3, 2]))
    if "height" in labels:
        _label(drawing, labels["height"], height_x + 6, (level_y["left"] + level_y["right"]) / 2)
    arrows = schema.get("pressureArrows", [])
    if not isinstance(arrows, list):
        raise ValueError("pressureArrows must be a list")
    for arrow in arrows:
        if not isinstance(arrow, dict) or arrow.get("side") not in {"left", "right"}:
            raise ValueError("each pressure arrow requires a left or right side")
        side = arrow["side"]
        direction = _text(arrow.get("direction"), "pressure arrow direction")
        _arrow(drawing, left_x if side == "left" else right_x, top - 8, direction, arrow.get("label"))
        drawing._lesson_arrow_directions.append(f"{side}:{direction}")
    relation = schema.get("pressureRelation")
    if relation is not None:
        if not isinstance(relation, dict):
            raise ValueError("pressureRelation must be an object")
        sign = relation.get("sign")
        if sign not in {"+", "-"}:
            raise ValueError("pressureRelation sign must be + or -")
        sealed_level = level_y[sealed_side]
        expected_sign = "+" if sealed_level < level_y[open_side] else "-"
        if sign != expected_sign:
            raise ValueError("pressureRelation sign conflicts with liquid levels")
        _label(drawing, relation.get("expression"), 210, 205, anchor="middle")
    return drawing


def _particle_model(schema: dict) -> Drawing:
    particles = schema.get("particles")
    if not isinstance(particles, list) or not particles:
        raise ValueError("particle_model schema requires particles")
    drawing = _drawing(420, 225)
    drawing.add(Rect(50, 35, 250, 150, strokeColor=INK, fillColor=None, strokeWidth=1.5))
    for particle in particles:
        if not isinstance(particle, dict):
            raise ValueError("each particle must be an object")
        x = 50 + 250 * _number(particle.get("x"), "particle x", 0, 1)
        y = 35 + 150 * _number(particle.get("y"), "particle y", 0, 1)
        drawing.add(Circle(x, y, 6, strokeColor=INK, fillColor=LIGHT_GRAY))
        if "label" in particle:
            _label(drawing, particle["label"], x + 8, y + 5)
    if "containerLabel" in schema:
        _label(drawing, schema["containerLabel"], 175, 16, anchor="middle")
    arrows = schema.get("arrows", [])
    if not isinstance(arrows, list):
        raise ValueError("particle arrows must be a list")
    for arrow in arrows:
        if not isinstance(arrow, dict):
            raise ValueError("particle arrow must be an object")
        _arrow(drawing, 305, 120, _text(arrow.get("direction"), "particle arrow direction"), arrow.get("label"))
        drawing._lesson_arrow_directions.append(_text(arrow.get("direction"), "particle arrow direction"))
    return drawing


def _molecular_interactions(schema: dict) -> Drawing:
    molecules = schema.get("molecules")
    if not isinstance(molecules, list) or len(molecules) < 2:
        raise ValueError("molecular_interactions schema requires two molecules")
    interaction = schema.get("interaction")
    if not isinstance(interaction, dict):
        raise ValueError("molecular_interactions schema requires interaction")
    drawing = _drawing(420, 225)
    centers = [(125, 118), (295, 118)]
    for molecule, (x, y) in zip(molecules[:2], centers):
        if not isinstance(molecule, dict):
            raise ValueError("molecule must be an object")
        atoms = molecule.get("atoms")
        if not isinstance(atoms, list) or not atoms:
            raise ValueError("molecule requires atoms")
        for index, atom in enumerate(atoms):
            angle_x = x + ((index % 2) * 26 - 13)
            angle_y = y + ((index // 2) * 28 - 14)
            drawing.add(Circle(angle_x, angle_y, 11, strokeColor=INK, fillColor=LIGHT_GRAY))
            _label(drawing, atom, angle_x, angle_y - 3, anchor="middle")
        _label(drawing, molecule.get("label"), x, 55, anchor="middle")
    drawing.add(Line(160, 118, 260, 118, strokeColor=MID_GRAY, strokeDashArray=[3, 2]))
    _label(drawing, interaction.get("label"), 210, 132, anchor="middle")
    direction = _text(interaction.get("direction"), "interaction direction")
    drawing._lesson_arrow_directions.append(direction)
    return drawing


def _cartesian_graph(visual: dict, schema: dict) -> Drawing:
    axes = visual.get("axes")
    if not isinstance(axes, list) or len(axes) != 2 or any(not isinstance(axis, str) or not axis.strip() for axis in axes):
        raise ValueError("cartesian_graph requires two schema axes")
    points = schema.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("cartesian_graph schema requires two points")
    values = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("graph point must contain x and y")
        values.append((_number(point[0], "point x"), _number(point[1], "point y")))
    trend = schema.get("trend")
    if not isinstance(trend, dict):
        raise ValueError("cartesian_graph schema requires trend")
    direction = _text(trend.get("direction"), "trend direction")
    drawing = _drawing(420, 225)
    origin_x, origin_y, graph_w, graph_h = 70, 40, 275, 140
    drawing.add(Line(origin_x, origin_y, origin_x + graph_w, origin_y, strokeColor=INK, strokeWidth=1.3))
    drawing.add(Line(origin_x, origin_y, origin_x, origin_y + graph_h, strokeColor=INK, strokeWidth=1.3))
    _label(drawing, axes[0], origin_x + graph_w / 2, 12, anchor="middle")
    _label(drawing, axes[1], 12, origin_y + graph_h / 2)
    x_values, y_values = zip(*values)
    x_span = max(x_values) - min(x_values) or 1
    y_span = max(y_values) - min(y_values) or 1
    transformed = [(origin_x + ((x - min(x_values)) / x_span) * graph_w, origin_y + ((y - min(y_values)) / y_span) * graph_h) for x, y in values]
    drawing.add(PolyLine(transformed, strokeColor=INK, strokeWidth=1.6))
    for x, y in transformed:
        drawing.add(Circle(x, y, 2.5, strokeColor=INK, fillColor=INK))
    _label(drawing, trend.get("label"), origin_x + graph_w - 3, origin_y + graph_h + 10, anchor="end")
    drawing._lesson_arrow_directions.append(direction)
    return drawing


def _energy_profile(schema: dict) -> Drawing:
    states = schema.get("states")
    transition = schema.get("transition")
    direction = _text(schema.get("reactionDirection"), "reaction direction")
    if not isinstance(states, list) or len(states) != 2 or not isinstance(transition, dict):
        raise ValueError("energy_profile requires two states and one transition")
    drawing = _drawing(420, 225)
    baseline, scale = 35, 150
    state_values = []
    for state in states:
        if not isinstance(state, dict):
            raise ValueError("energy state must be an object")
        state_values.append((_text(state.get("label"), "energy state label"), _number(state.get("energy"), "state energy", 0, 1)))
    transition_energy = _number(transition.get("energy"), "transition energy", 0, 1)
    left_y, right_y = (baseline + scale * value for _, value in state_values)
    peak_y = baseline + scale * transition_energy
    drawing.add(Line(55, baseline, 55, baseline + scale + 20, strokeColor=INK))
    _label(drawing, "Energy", 18, baseline + scale / 2)
    drawing.add(PolyLine([(80, left_y), (170, left_y), (220, peak_y), (270, right_y), (360, right_y)], strokeColor=INK, strokeWidth=1.8))
    _label(drawing, state_values[0][0], 125, left_y - 16, anchor="middle")
    _label(drawing, state_values[1][0], 315, right_y - 16, anchor="middle")
    _label(drawing, transition.get("label"), 220, peak_y + 8, anchor="middle")
    _arrow(drawing, 210, 18, "right", direction)
    drawing._lesson_arrow_directions.append(direction)
    return drawing


def reconstructed_visual_flowable(visual: dict) -> Drawing:
    """Build a chemistry diagram solely from explicit semantic schema fields."""
    if not isinstance(visual, dict):
        raise ValueError("visual must be an object")
    _string_list(visual, "entities")
    _string_list(visual, "relationships")
    _string_list(visual, "invariants")
    schema = _schema(visual)
    diagram_type = _diagram_type(visual)
    if diagram_type == "apparatus_tube":
        return _apparatus_tube(schema)
    if diagram_type == "particle_model":
        return _particle_model(schema)
    if diagram_type == "molecular_interactions":
        return _molecular_interactions(schema)
    if diagram_type == "cartesian_graph":
        return _cartesian_graph(visual, schema)
    return _energy_profile(schema)

"""
Movie Render Queue–style filename templates: {sequence_name}.{camera_name}.{frame_number}.{ext}
"""
import os
import re
from typing import Dict, Optional, Tuple

from errors import ImageSetError

# --- Defaults (plan) ---
DEFAULT_LEGACY_INPUT = "{sequence_name}.{camera_name}.{frame_number}.{ext}"
DEFAULT_LEGACY_OUTPUT = "{sequence_name}.{frame_number}.{ext}"
DEFAULT_STEREO_INPUT = "{sequence_name}.{camera_name}.{frame_number}.{ext}"
DEFAULT_STEREO_OUTPUT_OVER_UNDER = "{sequence_name}.StereoEquirect.{frame_number}.{ext}"
DEFAULT_STEREO_OUTPUT_SEPARATE = "{eye}/{sequence_name}.Equirect.{frame_number}.{ext}"

_SUPPORTED_EXT_CAPTURE = r"(?P<ext>jpe?g|png)"

# User-facing MRQ-style tokens (dropdown). Aliases map to the same regex group name.
KEYWORD_ALIASES = {
    "Viewport": "camera_name",
    "viewport": "camera_name",
    "Frame": "frame_number",
    "LevelSequence": "level_sequence",
}

INPUT_KEYWORDS_LEGACY: Tuple[str, ...] = (
    "level_name",
    "sequence_name",
    "job_name",
    "frame_rate",
    "date",
    "time",
    "year",
    "month",
    "day",
    "version",
    "job_author",
    "frame_number",
    "frame_number_shot",
    "frame_number_rel",
    "frame_number_shot_rel",
    "camera_name",
    "shot_name",
    "render_pass",
    "project_dir",
    "output_resolution",
    "output_width",
    "output_height",
    "ext",
    # legacy aliases (still offered)
    "LevelSequence",
    "Viewport",
    "Frame",
)

INPUT_KEYWORDS_STEREO: Tuple[str, ...] = INPUT_KEYWORDS_LEGACY

OUTPUT_KEYWORDS_LEGACY: Tuple[str, ...] = tuple(
    k
    for k in INPUT_KEYWORDS_LEGACY
    if k not in ("Viewport", "Frame", "camera_name")
)

OUTPUT_KEYWORDS_STEREO_OVER_UNDER: Tuple[str, ...] = OUTPUT_KEYWORDS_LEGACY

OUTPUT_KEYWORDS_STEREO_SEPARATE: Tuple[str, ...] = OUTPUT_KEYWORDS_LEGACY + ("eye",)

_TOKEN_RE = re.compile(r"\{([^{}]+)\}")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_placeholder(raw: str) -> str:
    """Map alias token to canonical regex / field key."""
    key = raw.strip()
    return KEYWORD_ALIASES.get(key, key)


def dotted_ext_from_capture(capture: str) -> str:
    """Map regex ext group (png / jpeg / jpg) to '.png' or '.jpeg'."""
    d = _normalize_ext_token(capture)
    b = d.lstrip(".").lower()
    if b == "jpg":
        b = "jpeg"
    if b not in ("jpeg", "png"):
        raise ImageSetError(
            f"Unsupported extension in filename: {capture!r} (supported: jpeg, jpg, png)."
        )
    return "." + b


def _normalize_ext_token(raw: str) -> str:
    """Return extension with leading dot, lowercase."""
    s = raw.strip().lower()
    if not s:
        return ".jpeg"
    if s.startswith("."):
        s = s[1:]
    if s == "jpg":
        s = "jpeg"
    return f".{s}"


def placeholders_in_template(template: str) -> Tuple[str, ...]:
    """Order-preserving unique canonical placeholder names."""
    seen = []
    found = set()
    for m in _TOKEN_RE.finditer(template):
        can = normalize_placeholder(m.group(1))
        if can not in found:
            found.add(can)
            seen.append(can)
    return tuple(seen)


def compile_filename_pattern(template: str) -> re.Pattern:
    """
    Build a regex that matches the full basename (no directory), case-insensitive for extension.
    Duplicate placeholders (after alias normalization) are an error.
    """
    if not template or not str(template).strip():
        raise ImageSetError("Naming template is empty.")

    group_counts: Dict[str, int] = {}
    for m in _TOKEN_RE.finditer(template):
        can = normalize_placeholder(m.group(1))
        group_counts[can] = group_counts.get(can, 0) + 1

    dupes = [k for k, v in group_counts.items() if v > 1]
    if dupes:
        raise ImageSetError(
            "Duplicate placeholders in template (after aliases): " + ", ".join(sorted(dupes))
        )

    pieces = []
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        pieces.append(re.escape(template[pos : m.start()]))
        raw = m.group(1).strip()
        can = normalize_placeholder(raw)
        if can == "ext":
            pieces.append(_SUPPORTED_EXT_CAPTURE)
        else:
            if not _IDENT.match(can):
                raise ImageSetError(
                    f"Invalid placeholder {{{raw}}}: use letters, digits, and underscores only "
                    f"(or a supported alias such as {{camera_name}})."
                )
            pieces.append(f"(?P<{can}>[^/\\\\]+)")
        pos = m.end()
    pieces.append(re.escape(template[pos:]))
    return re.compile("^" + "".join(pieces) + "$", re.IGNORECASE)


def parse_basename_with_template(template: str, file_name: str) -> Optional[Dict[str, str]]:
    pat = compile_filename_pattern(template)
    base = file_name.split(os.path.sep)[-1]
    m = pat.match(base)
    if not m:
        return None
    gd = dict(m.groupdict())
    if "ext" in gd and gd["ext"] is not None:
        gd["ext"] = gd["ext"].lower()
    return gd


def validate_input_template(template: str) -> None:
    compile_filename_pattern(template)  # dupes / syntax
    ph = frozenset(placeholders_in_template(template))
    need = frozenset(["camera_name", "frame_number", "ext"])
    missing = need - ph
    if missing:
        raise ImageSetError(
            "Input naming template must include {camera_name}, {frame_number}, and {ext}. "
            f"Missing: {', '.join('{' + x + '}' for x in sorted(missing))}."
        )


def validate_output_template(
    output_template: str,
    input_template: str,
    *,
    merger: str,
    stereo_mode_separate_eyes: bool,
) -> None:
    compile_filename_pattern(output_template)
    out_ph = frozenset(placeholders_in_template(output_template))
    in_ph = frozenset(placeholders_in_template(input_template))

    if "eye" in out_ph and merger == "legacy":
        raise ImageSetError("Output template must not use {eye} for Config Merger.")

    if merger == "stereo":
        if stereo_mode_separate_eyes:
            if "eye" not in out_ph:
                raise ImageSetError(
                    "For equirectangular mono (separate eyes), output template must include {eye}."
                )
            if "camera_name" in out_ph:
                raise ImageSetError("Output template must not include {camera_name} for stereo export.")
        else:
            if "eye" in out_ph:
                raise ImageSetError(
                    "For over/under stereo, output template must not include {eye}."
                )

    need_out = frozenset(["frame_number", "ext"])
    miss = need_out - out_ph
    if miss:
        raise ImageSetError(
            "Output naming template must include {frame_number} and {ext}. "
            f"Missing: {', '.join('{' + x + '}' for x in sorted(miss))}."
        )

    extra = out_ph - in_ph
    if merger == "stereo" and stereo_mode_separate_eyes:
        extra = extra - frozenset(["eye"])
    if extra:
        raise ImageSetError(
            "Every output placeholder must appear in the input template (so values exist). "
            f"Not in input: {', '.join('{' + x + '}' for x in sorted(extra))}."
        )

    if "ext" in out_ph:
        # disallow exr in template literal is harder; check at render time
        pass


def _fields_for_render(fields: Dict[str, str], eye: Optional[str] = None) -> Dict[str, str]:
    r = dict(fields)
    if eye is not None:
        r["eye"] = eye
    if "ext" in r:
        r["ext"] = r["ext"].lower().lstrip(".")
        if r["ext"] == "jpg":
            r["ext"] = "jpeg"
    return r


def render_output_relative(output_template: str, fields: Dict[str, str], *, eye: Optional[str] = None) -> str:
    """Relative path under output root; creates no absolute path."""
    validate_ext_not_exr(fields.get("ext", ""))
    f = _fields_for_render(fields, eye=eye)

    def repl(m):
        raw = m.group(1).strip()
        can = normalize_placeholder(raw)
        key = can
        if key == "eye":
            if eye is None:
                raise ImageSetError("Internal error: {eye} in output but no eye value.")
            return eye
        if key not in f:
            raise ImageSetError(f"Missing value for output placeholder {{{key}}}.")
        val = f[key]
        if key == "ext":
            if val.lower() in ("exr",):
                raise ImageSetError("EXR output is not supported.")
            return val
        if os.path.sep in val or "/" in val or "\\" in val:
            raise ImageSetError(f"Invalid character in field {{{key}}}: path separators are not allowed.")
        if ".." in val:
            raise ImageSetError(f"Invalid value for {{{key}}}: '..' is not allowed.")
        return val

    out = _TOKEN_RE.sub(repl, output_template)
    # normalize slashes for OS
    parts = re.split(r"[\\/]+", out)
    return os.path.join(*parts) if parts else out


def validate_ext_not_exr(ext: str) -> None:
    e = ext.lower().strip().lstrip(".")
    if e == "exr":
        raise ImageSetError("EXR files are not supported.")


def precheck_input_file_ext(file_name: str) -> None:
    low = file_name.lower()
    if low.endswith(".exr"):
        raise ImageSetError(f"EXR is not supported: '{file_name}'.")
    if not low.endswith((".jpeg", ".jpg", ".png")):
        # still allow regex to fail later
        pass


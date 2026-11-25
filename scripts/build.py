#!/usr/bin/env python3
"""Generate the Xcode Zed themes from the Vine VS Code Xcode theme.

Usage: python3 scripts/build.py [path/to/vine/themes]

Each bordered VS Code palette becomes one Zed theme
family in themes/<palette>.json. Syntax colors are resolved from the VS Code
tokenColors (TextMate) and semanticTokenColors rules, so Zed's tree-sitter
captures get the same colors the VS Code themes show.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT.parent / "vine" / "themes"
OUT = ROOT / "themes"
SCHEMA = "https://zed.dev/schema/themes/v0.2.0.json"
AUTHOR = "Dallas Gere"
TRANSPARENT = "#00000000"
COMMENT_LIFT = 0.36  # how far comments sit from the background toward white

# "Edge" pass: sharpens depth and hierarchy without touching hues.
# Contrast values are WCAG ratios; lightness values are OKLab L (0-1).
EDGE_SHELL_DROP = 0.05  # how much darker the shell (panels, bars) sits than the editor
EDGE_SHELL_FLOOR = 0.12  # never push the shell below this lightness
EDGE_BORDER = 1.45  # divider contrast against the shell
EDGE_BORDER_VARIANT = 1.25  # quieter dividers inside panels
EDGE_SELECTED_TINT = 0.25  # how much accent bleeds into selected rows
EDGE_SELECTED_TEXT = 6.0  # UI text contrast on a selected row
EDGE_HOVER_LIFT = 0.035  # hover rows, lifted from the panel
EDGE_CHROMA = 1.12  # syntax saturation boost
EDGE_SYNTAX_MIN = 5.5  # every syntax color stays at least this readable
EDGE_COMMENT = 3.8  # comments: recessive but comfortable
EDGE_LINE_NUMBER = 3.0  # inactive gutter numbers recede so code leads
EDGE_TEXT_MIN = 7.0  # UI text against panels
EDGE_TEXT_MUTED_MIN = 4.5

SKIP_SUFFIXES = ("-unbordered", "-uniform")
SKIP_PALETTES = {"atom-one-dark"}
ONLY_PALETTES = {"xcode-26-dark"}  # this pack builds just the Xcode themes

# Xcode's classic Default (Dark) palette: the softer pastel set Xcode is known for.
# Tree-sitter can't tell project symbols from SDK ones, so each role gets its own
# Xcode color for Tokyo Night-style separation.
XCODE = {
    # Xcode draws plain text at #dddee2, but its thin SF Mono strokes make that read
    # softer than Zed's fuller glyphs, so plain text sits lower here to feel the same.
    "plain": "#c5c7cd",  # soft, slightly cool white for locals, parameters and labels
    "punctuation": "#979ba4",  # brackets, dots, operators: present but quieter than names
    "keyword": "#ff7ab2",
    "string": "#ff8170",
    "number": "#d9c97c",
    "preprocessor": "#ffa14f",
    "attribute": "#cc9768",
    "type_declaration": "#6bdfff",
    "declaration": "#5fbcd6",
    "project_type": "#8fd4c6",  # Xcode #acf2e4, toned down to match the palette's lightness
    "project_member": "#86cdbf",
    "sdk_type": "#dabaff",
    "sdk_function": "#c39af2",
    "url": "#6699ff",
    "comment": "#909dab",  # Xcode #7f8c98, lifted so it stays legible
}

# folke/tokyonight.nvim "moon" palette, mapped the way that theme maps its
# tree-sitter groups (lua/tokyonight/groups/treesitter.lua).
TOKYO = {
    "fg": "#c8d3f5",
    "fg_dark": "#828bb8",
    "comment": "#636da6",
    "blue": "#82aaff",
    "blue1": "#65bcff",
    "blue5": "#89ddff",
    "blue6": "#b4f9f8",
    "cyan": "#86e1fc",
    "green": "#c3e88d",
    "green1": "#4fd6be",
    "magenta": "#c099ff",
    "purple": "#fca7ea",
    "orange": "#ff966c",
    "yellow": "#ffc777",
    "red": "#ff757f",
    "bg": "#222436",
}


def blend(fg, alpha, bg):
    """tokyonight's Util.blend: straight sRGB mix, alpha of fg over bg."""
    a = [int(fg[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02x}" for x, y in zip(a, b))


# VS Code's git color roles (green added, yellow changed, red deleted, blue
# changed bar in the gutter) in shades taken from Xcode's own palette.
XCODE_GIT_DIM = 0.92  # gutter bars: this much of the color over the editor, muted like VS Code's
XCODE_GIT = {
    # Explorer and tab labels.
    "added": XCODE["project_member"],  # Xcode's mint green
    "modified": XCODE["number"],  # Xcode's number yellow
    "deleted": "#e8786a",  # Xcode's string red, eased back
    "conflict": "#e8955a",  # Xcode's preprocessor orange, eased back
    "ignored": "#7f8c98",  # Xcode's own comment gray
    # Gutter bars.
    **{f"gutter_{k}": blend(c, XCODE_GIT_DIM, "#1e2025") for k, c in {
        "added": XCODE["project_member"],
        "modified": XCODE["declaration"],  # Xcode's declaration blue
        "deleted": XCODE["string"],
    }.items()},
}


# Per-palette corrections, applied on top of the VS Code source.
#   file/name: output file stem and theme name
#   remap:     color swaps applied everywhere in the source theme
#   colors:    VS Code workbench keys to set
#   syntax:    Zed captures to set directly
#   chroma:    syntax saturation boost (EDGE_CHROMA when absent)
#   style:     Zed keys set directly
#   edge:      overrides for the EDGE_* contrast constants; vc_strength (0-1)
#              blends the gutter diff bars toward the editor background
PALETTES = {
    # Keeps the Vine Tokyo Night surfaces; syntax from folke's tokyonight moon.
    "tokyo-night": {
        "chroma": 1.0,  # use folke's colors as-is
        "syntax": {
            **{k: c for c, ks in {
                TOKYO["comment"]: ("comment", "comment.doc"),
                TOKYO["purple"]: ("keyword", "keyword.coroutine", "keyword.return", "keyword.modifier",
                                  "keyword.type", "keyword.control"),
                TOKYO["magenta"]: ("keyword.conditional", "keyword.repeat", "keyword.exception",
                                   "keyword.function", "constructor"),
                TOKYO["cyan"]: ("keyword.import", "keyword.export", "keyword.directive", "preproc",
                                "function.special", "attribute", "namespace"),
                TOKYO["blue1"]: ("keyword.storage", "type", "type.parameter", "enum", "constant.builtin",
                                 "function.builtin", "string.special", "string.special.symbol"),
                TOKYO["blue5"]: ("operator", "keyword.operator", "punctuation.delimiter",
                                 "punctuation.special", "punctuation.list_marker"),
                TOKYO["blue"]: ("function", "function.call", "function.definition", "function.method",
                                "function.method.call", "label", "title", "link_uri"),
                TOKYO["fg_dark"]: ("punctuation", "punctuation.bracket"),
                TOKYO["green"]: ("string", "text.literal"),
                TOKYO["green1"]: ("property", "variable.member", "property.json_key", "link_text"),
                TOKYO["orange"]: ("constant", "boolean", "number", "variant", "punctuation.markup"),
                TOKYO["yellow"]: ("variable.parameter", "string.doc"),
                TOKYO["red"]: ("variable.special", "tag", "tag.doctype"),
                TOKYO["fg"]: ("variable", "embedded", "primary", "emphasis", "emphasis.strong"),
            }.items() for k in ks},
            "string.escape": TOKYO["magenta"],
            "string.regex": TOKYO["blue6"],
            "type.builtin": blend(TOKYO["blue1"], 0.8, TOKYO["bg"]),
            "type.interface": blend(TOKYO["blue1"], 0.7, TOKYO["fg"]),
        },
    },
    # Xcode's classic Default (Dark). Surfaces, accent and selection live in the
    # ../vine source; this adds Zed-only role colors and the macOS frame.
    "xcode-26-dark": {
        "file": "xcode",
        "name": "Xcode Custom",
        "chroma": 1.0,  # keep Xcode's pastels exactly
        # Soft graphite surfaces close to real Xcode (#292a30), a step darker, with a
        # shell only slightly below the editor so the frame doesn't glare against the code.
        "colors": {
            "titleBar.activeBackground": "#191b20",
            "titleBar.inactiveBackground": "#191b20",
            "sideBar.background": "#191b20",
            "statusBar.background": "#191b20",
            "editorGroupHeader.tabsBackground": "#191b20",
            "tab.inactiveBackground": "#191b20",
            "editor.foreground": XCODE["plain"],
            "editor.background": "#1e2025",
            "tab.activeBackground": "#1e2025",
            "editor.lineHighlightBackground": "#25272d",
            "editorWidget.background": "#27292f",
            "menu.background": "#27292f",
            "input.background": "#27292f",
            "editorInlayHint.background": "#27292f",
            "tree.indentGuidesStroke": "#2c2e34",
            "editorIndentGuide.background1": "#2c2e34",
            "editor.selectionBackground": "#313949",
        },
        # Source control with VS Code's roles (distinct added, changed and deleted
        # colors) in Xcode's shades; see XCODE_GIT.
        "style": {
            **{f"{name}{suffix}": c + (a or "")
               for name, c in {
                   "created": XCODE_GIT["added"], "modified": XCODE_GIT["modified"],
                   "deleted": XCODE_GIT["deleted"], "renamed": XCODE_GIT["added"],
                   "conflict": XCODE_GIT["conflict"], "ignored": XCODE_GIT["ignored"],
               }.items()
               for suffix, a in (("", None), (".background", "2e"), (".border", "66"))},
            "success": XCODE_GIT["added"],
            "version_control.added": XCODE_GIT["gutter_added"],
            "version_control.modified": XCODE_GIT["gutter_modified"],
            "version_control.deleted": XCODE_GIT["gutter_deleted"],
            "version_control.renamed": XCODE_GIT["gutter_modified"],
            "version_control.conflict": XCODE_GIT["conflict"],
            "version_control.ignored": XCODE_GIT["ignored"],
            "version_control.word_added": XCODE_GIT["gutter_added"] + "5c",
            "version_control.word_deleted": XCODE_GIT["gutter_deleted"] + "5c",
        },
        "edge": {"border": 1.32, "border_variant": 1.15, "selected_lift": 0.1, "selected_tint": 0.06, "shell_drop": 0.02},
        "syntax": {
            **{k: XCODE["comment"] for k in ("comment", "comment.doc")},
            # Calls: SDK purple (most calls hit library methods). Definitions: declaration blue.
            **{k: XCODE["sdk_function"] for k in (
                "function", "function.call", "function.method", "function.method.call", "function.builtin")},
            "function.definition": XCODE["declaration"],
            # Types: your types in mint, built-ins (string, Promise...) in lavender.
            **{k: XCODE["project_type"] for k in (
                "type", "type.interface", "type.parameter", "enum", "constructor", "namespace")},
            "type.builtin": XCODE["sdk_type"],
            # Locals and parameters in plain text; properties pick up Xcode's member teal so
            # `user.name` chains read as structure instead of a wall of white.
            **{k: XCODE["plain"] for k in ("variable", "label", "embedded", "primary", "emphasis", "emphasis.strong")},
            # Parameter names where they're declared take Xcode's declaration blue, as in
            # Xcode's signatures; Zed only marks the declaration, so uses in the body stay plain.
            "variable.parameter": XCODE["declaration"],
            **{k: XCODE["project_member"] for k in ("property", "variable.member")},
            "keyword.operator": XCODE["keyword"],
            # JSON keys, enum cases, and tags keep Xcode's muted teal; constants stay with literals.
            **{k: XCODE["project_member"] for k in ("property.json_key", "variant", "tag", "tag.doctype")},
            **{k: XCODE["number"] for k in ("constant", "string.special.symbol")},
            "title": XCODE["type_declaration"],
            "link_uri": XCODE["url"],
            # Pin literals, keywords, and punctuation to Xcode's own values so the
            # ../vine source can retune its VS Code colors without dimming these.
            **{k: XCODE["keyword"] for k in (
                "keyword", "keyword.conditional", "keyword.control", "keyword.coroutine", "keyword.exception",
                "keyword.export", "keyword.function", "keyword.import", "keyword.modifier", "keyword.repeat",
                "keyword.return", "keyword.storage", "keyword.type", "boolean", "constant.builtin",
                "variable.special")},
            **{k: XCODE["string"] for k in (
                "string", "string.doc", "string.special", "text.literal", "link_text")},
            **{k: XCODE["number"] for k in ("number", "string.escape", "string.regex")},
            **{k: XCODE["preprocessor"] for k in ("preproc", "keyword.directive", "function.special")},
            **{k: XCODE["attribute"] for k in ("attribute", "selector", "selector.pseudo")},
            **{k: XCODE["punctuation"] for k in (
                "operator", "punctuation", "punctuation.bracket", "punctuation.delimiter", "punctuation.list_marker",
                "punctuation.markup", "punctuation.special")},
        },
    },
}

# Tokyo Night Neutral shares Tokyo Night's folke syntax; only its surfaces differ.
PALETTES["tokyo-night-neutral"] = PALETTES["tokyo-night"]

# Rosé Pine's own colors on neutral graphite, with a calmer hierarchy than the
# VS Code port: one color per role, softer foreground, quiet punctuation.
ROSE = {
    "base": "#1e2024",  # editor
    "shell": "#191b1f",  # panels, tabs, title and status bars
    "surface": "#2a2c31",  # popovers, inputs, inlay hints
    "text": "#d6d4e4",  # a touch under Rosé Pine's #e0def4 so plain code doesn't glare
    "subtle": "#908caa",
    "muted": "#6e6a86",
    "love": "#eb6f92",
    "gold": "#f6c177",
    "rose": "#ebbcba",
    "pine": "#5ea5c0",  # pine, lifted so keywords stay readable on the dark base
    "foam": "#9ccfd8",
    "iris": "#c4a7e7",
    "green": "#8fbf96",
}
ROSE_BG = "charcoal"  # Rosé Pine: Neutral background: "charcoal", "black" or "graphite"

PALETTES["rose-pine-neutral"] = {
    "chroma": 1.0,  # keep Rosé Pine's soft pastels, no saturation boost
    "colors": {
        "editor.background": ROSE["base"],
        "editor.foreground": ROSE["text"],
        "editor.lineHighlightBackground": "#26282d",
        "editor.selectionBackground": "#383a40",
        **{k: ROSE["shell"] for k in ("titleBar.activeBackground", "sideBar.background")},
        **{k: ROSE["surface"] for k in ("editorWidget.background", "menu.background", "input.background")},
        "editorCursor.foreground": ROSE["rose"],
        "terminal.foreground": ROSE["text"],
        "terminal.ansiBlack": "#3a3c42",
        "terminal.ansiBrightBlack": ROSE["muted"],
        "terminal.ansiGreen": ROSE["green"],
        "terminal.ansiBrightGreen": ROSE["green"],
        "terminal.ansiWhite": ROSE["text"],
    },
    "style": {
        # Guides and whitespace fade into the background instead of drawing lines.
        "editor.wrap_guide": "#2c2e33",
        "editor.active_wrap_guide": "#383a40",
        "editor.invisible": "#45474d",
        "editor.document_highlight.bracket_background": ROSE["rose"] + "1f",
        # Diagnostics and file status in the Rosé Pine palette, not VS Code defaults.
        **{f"{name}{suffix}": c + (a or "")
           for name, c in {
               "error": ROSE["love"], "warning": ROSE["gold"], "info": ROSE["foam"],
               "success": ROSE["green"], "created": ROSE["green"], "modified": ROSE["gold"],
               "deleted": ROSE["love"], "renamed": ROSE["foam"], "conflict": ROSE["love"],
               "ignored": ROSE["muted"], "hint": ROSE["subtle"],
           }.items()
           for suffix, a in (("", None), (".background", "1a"), (".border", "66"))},
        "hint.background": ROSE["surface"],
        "version_control.added": ROSE["green"],
        "version_control.modified": ROSE["gold"],
        "version_control.deleted": ROSE["love"],
        "version_control.renamed": ROSE["foam"],
        "version_control.conflict": ROSE["love"],
        "version_control.ignored": ROSE["muted"],
    },
    "syntax": {
        **{k: c for c, ks in {
            ROSE["muted"]: ("comment", "comment.doc"),
            # Keywords in pine; operators and punctuation step back so keywords don't flood the line.
            ROSE["pine"]: ("keyword", "keyword.conditional", "keyword.control", "keyword.coroutine",
                           "keyword.directive", "keyword.exception", "keyword.export", "keyword.function",
                           "keyword.import", "keyword.modifier", "keyword.repeat", "keyword.return",
                           "keyword.storage", "keyword.type", "keyword.operator", "preproc",
                           "string.escape", "string.regex"),
            ROSE["subtle"]: ("operator", "punctuation", "punctuation.bracket", "punctuation.delimiter",
                             "punctuation.list_marker", "punctuation.markup", "punctuation.special"),
            # Functions alone get rose, so calls stand out from values.
            ROSE["rose"]: ("function", "function.builtin", "function.call", "function.definition",
                           "function.method", "function.method.call"),
            ROSE["love"]: ("function.special", "diff.minus"),
            ROSE["green"]: ("diff.plus",),
            ROSE["gold"]: ("string", "string.doc", "string.special", "text.literal", "link_text"),
            # Literal values (numbers, booleans, constants) in iris, distinct from functions.
            ROSE["iris"]: ("number", "boolean", "constant", "constant.builtin", "variant", "enum",
                           "string.special.symbol", "variable.special", "attribute", "selector",
                           "selector.pseudo"),
            ROSE["foam"]: ("type", "type.builtin", "type.interface", "type.parameter", "constructor",
                           "namespace", "tag", "tag.doctype", "title", "property.json_key", "link_uri"),
            ROSE["text"]: ("variable", "variable.parameter", "variable.member", "property", "label",
                           "embedded", "emphasis", "emphasis.strong", "primary"),
        }.items() for k in ks},
    },
    "edge": {"shell_drop": 0.02, "vc_strength": 0.5},
}

# Poimandres, refined: the VS Code port leaned on one blue-gray for keywords,
# punctuation and types alike. Here each role takes its own Poimandres color:
# blue keywords, light-blue functions, mint strings, pale-yellow types, pink
# literals, hot red for exceptions, and punctuation stepped back to gray.
POI = {
    "white": "#e4f0fb",
    "text": "#cbd5e0",
    "gray": "#8a90b0",
    "comment": "#767c9d",
    "mint": "#5de4c7",
    "blue": "#89ddff",
    "light_blue": "#add7ff",
    "keyword": "#91b4d5",
    "yellow": "#fffac2",
    "pink": "#fcc5e9",
    "red": "#d0679d",
}
PALETTES["poimandres"] = PALETTES["poimandres-neutral"] = {
    "chroma": 1.0,  # Poimandres pastels are already vivid
    "syntax": {
        **{k: c for c, ks in {
            POI["comment"]: ("comment", "comment.doc"),
            POI["keyword"]: ("keyword", "keyword.conditional", "keyword.control", "keyword.coroutine",
                             "keyword.directive", "keyword.export", "keyword.function", "keyword.import",
                             "keyword.modifier", "keyword.repeat", "keyword.return", "keyword.storage",
                             "keyword.type", "keyword.operator", "preproc", "variable.special",
                             "attribute", "selector", "selector.pseudo"),
            POI["red"]: ("keyword.exception", "diff.minus"),
            POI["gray"]: ("operator", "punctuation", "punctuation.bracket", "punctuation.delimiter",
                          "punctuation.list_marker", "punctuation.markup", "punctuation.special"),
            POI["light_blue"]: ("function", "function.builtin", "function.call", "function.definition",
                                "function.method", "function.method.call", "property.json_key", "link_text"),
            POI["blue"]: ("function.special", "string.escape", "string.regex", "tag", "tag.doctype",
                          "link_uri"),
            POI["mint"]: ("string", "string.doc", "string.special", "text.literal", "title", "diff.plus"),
            POI["yellow"]: ("type", "type.builtin", "type.interface", "type.parameter", "constructor",
                            "namespace", "enum"),
            POI["pink"]: ("number", "boolean", "constant", "constant.builtin", "variant",
                          "string.special.symbol"),
            POI["white"]: ("variable", "variable.parameter", "label", "embedded", "emphasis",
                           "emphasis.strong", "primary"),
            POI["text"]: ("property", "variable.member"),
        }.items() for k in ks},
    },
}

# All-black variants: editor, panels, tabs and bars at #000000, popovers a step
# above, and dividers, guides and rows kept faint so nothing draws hard lines.
BLACK = {
    "base": "#000000",
    "surface": "#141518",  # popovers, menus, inputs, inlay hints
    "line": "#0e0f11",  # current line
    "wrap": "#121315",
    "wrap_active": "#1c1d20",
    "invisible": "#2e2f33",
    "indent": "#141517",
    "indent_active": "#26272b",
    "selection": None,  # keep the palette's own
}
# Charcoal: neutral (no tint) and a few steps above black, so it reads dark
# without going jet black, and stays below the graphite set so it isn't gray.
CHARCOAL = {
    "base": "#17181b",  # editor
    "shell": "#131417",  # panels, tabs, title and status bars, a hair darker
    "surface": "#212226",
    "line": "#1d1e21",
    "wrap": "#1e1f22",
    "wrap_active": "#2a2b2f",
    "invisible": "#3a3b3f",
    "indent": "#202124",
    "indent_active": "#303135",
    "selection": "#303136",
    # Rows lift from a dark-but-not-black shell, so smaller steps than BLACK's.
    "edge": {"hover_lift": 0.035, "selected_lift": 0.07},
}
BLACK_EDGE = {
    "shell_drop": 0.0, "shell_floor": 0.0,  # let the shell and editor both sit at pure black
    "border": 1.2, "border_variant": 1.1,  # faint dividers between panels and tabs
    "hover_lift": 0.2, "selected_lift": 0.26, "selected_tint": 0.12,  # rows stay visible on black
    "selected_text": 5.0,
}


def black(config, name=None, file=None, bg=BLACK):
    """The same palette on an all-black (or other very dark) background."""
    BLACK = bg  # noqa: N806 (reads below as the chosen background set)
    out = {**config, "colors": {**config.get("colors", {}),
        "editor.background": BLACK["base"],
        **{k: BLACK.get("shell", BLACK["base"]) for k in ("titleBar.activeBackground", "sideBar.background")},
        **({"editor.selectionBackground": BLACK["selection"]} if BLACK.get("selection") else {}),
        **{k: BLACK["surface"] for k in ("editorWidget.background", "menu.background", "input.background",
                                         "editorInlayHint.background")},
        "editor.lineHighlightBackground": BLACK["line"],
    }, "style": {**config.get("style", {}),
        "editor.wrap_guide": BLACK["wrap"],
        "editor.active_wrap_guide": BLACK["wrap_active"],
        "editor.invisible": BLACK["invisible"],
        "editor.subheader.background": BLACK["surface"],
        "hint.background": BLACK["surface"],
        **{f"{p}.indent_guide": BLACK["indent"] for p in ("editor", "panel")},
        **{f"{p}.indent_guide_active": BLACK["indent_active"] for p in ("editor", "panel")},
        "panel.indent_guide_hover": BLACK["indent_active"],
    }, "edge": {**config.get("edge", {}), **BLACK_EDGE, **BLACK.get("edge", {})}}
    if name:
        out["name"], out["file"] = name, file
    return out


if ROSE_BG in ("black", "charcoal"):
    PALETTES["rose-pine-neutral"] = black(PALETTES["rose-pine-neutral"], bg=BLACK if ROSE_BG == "black" else CHARCOAL)

# Extra themes built from a source palette: source -> [config, ...].
VARIANTS = {
    "tokyo-night": [black(PALETTES["tokyo-night"], "Vine: Tokyo Night Black", "tokyo-night-black")],
    "poimandres": [black(PALETTES["poimandres"], "Vine: Poimandres Black", "poimandres-black")],
}


def remap_colors(node, mapping):
    """Swap colors throughout a parsed theme (alpha suffixes are kept)."""
    if isinstance(node, dict):
        return {k: remap_colors(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [remap_colors(v, mapping) for v in node]
    if isinstance(node, str) and node.startswith("#") and node[:7].lower() in mapping:
        return mapping[node[:7].lower()] + node[7:].lower()
    return node

# Zed syntax capture -> (semantic token keys, TextMate scope stacks).
# Semantic keys win when the theme defines them (as in VS Code). Otherwise the
# first TextMate stack (outermost scope first) that matches a rule is used.
SYNTAX = {
    "attribute": ([], [["meta.tag", "entity.other.attribute-name"]]),
    "boolean": ([], [["constant.language.boolean.true"]]),
    "comment": (["comment"], [["comment.line.double-slash"]]),
    "comment.doc": (["comment"], [["comment.block.documentation"]]),
    "constant": ([], [["variable.other.constant"]]),
    "constant.builtin": ([], [["constant.language.null"]]),
    "constructor": (["class"], [["meta.function-call", "entity.name.type.class"], ["entity.name.class"]]),
    "diff.minus": ([], [["markup.deleted"]]),
    "diff.plus": ([], [["markup.inserted"]]),
    "embedded": ([], [["meta.embedded"]]),
    "emphasis": ([], [["markup.italic"]]),
    "emphasis.strong": ([], [["markup.bold"]]),
    "enum": (["enum"], [["entity.name.type.enum"]]),
    "function": (["function"], [["meta.function-call", "entity.name.function"]]),
    "function.builtin": (["function.defaultLibrary", "function"], [["support.function"]]),
    "function.call": (["function"], [["meta.function-call", "entity.name.function"]]),
    "function.definition": (
        ["function.declaration", "function.definition", "function"],
        [["meta.definition.function", "entity.name.function"]],
    ),
    "function.method": (["method"], [["meta.method-call", "entity.name.function"], ["meta.function-call", "entity.name.function"]]),
    "function.method.call": (["method"], [["meta.method-call", "entity.name.function"], ["meta.function-call", "entity.name.function"]]),
    "function.special": (["macro"], [["support.macro"], ["entity.name.function.preprocessor"]]),
    "hint": ([], []),
    "keyword": (["keyword"], [["keyword.control"]]),
    "keyword.conditional": ([], [["keyword.control.conditional"]]),
    "keyword.control": ([], [["keyword.control"]]),
    "keyword.coroutine": ([], [["keyword.control.flow"]]),
    "keyword.directive": ([], [["keyword.control.directive"], ["meta.preprocessor"]]),
    "keyword.exception": ([], [["keyword.control.trycatch"]]),
    "keyword.export": ([], [["keyword.control.export"]]),
    "keyword.function": ([], [["storage.type.function"]]),
    "keyword.import": ([], [["keyword.control.import"]]),
    "keyword.modifier": (["modifier"], [["storage.modifier"]]),
    "keyword.operator": ([], [["keyword.operator.expression"], ["keyword.operator.new"]]),
    "keyword.repeat": ([], [["keyword.control.loop"]]),
    "keyword.return": ([], [["keyword.control.flow"]]),
    "keyword.storage": ([], [["storage.type"]]),
    "keyword.type": ([], [["storage.type"]]),
    "label": ([], [["entity.name.label"]]),
    "link_text": ([], [["string.other.link.title.markdown"]]),
    "link_uri": ([], [["markup.underline.link"]]),
    "namespace": (["namespace"], [["entity.name.namespace"]]),
    "number": (["number"], [["constant.numeric.decimal"]]),
    "operator": (["operator"], [["keyword.operator.assignment"]]),
    "predictive": ([], []),
    "preproc": ([], [["meta.preprocessor"], ["keyword.control.directive"]]),
    "primary": ([], []),
    "property": (["property"], [["variable.other.property"]]),
    "property.json_key": ([], [["source.json", "support.type.property-name.json"]]),
    "punctuation": ([], [["punctuation"]]),
    "punctuation.bracket": ([], [["meta.brace.round.ts"], ["punctuation.definition.block"]]),
    "punctuation.delimiter": ([], [["punctuation.separator.comma"], ["punctuation.terminator.statement"]]),
    "punctuation.list_marker": ([], [["markup.list.unnumbered.markdown", "punctuation.definition.list.begin.markdown"]]),
    "punctuation.markup": ([], [["markup.heading.markdown", "punctuation.definition.heading.markdown"]]),
    "punctuation.special": ([], [["string.template", "meta.template.expression", "punctuation.definition.template-expression.begin"]]),
    "selector": ([], [["source.css", "entity.other.attribute-name.class.css"]]),
    "selector.pseudo": ([], [["source.css", "entity.other.attribute-name.pseudo-class.css"]]),
    "string": (["string"], [["string.quoted.double"]]),
    "string.doc": (["string"], [["string.quoted.docstring"]]),
    "string.escape": ([], [["string.quoted.double", "constant.character.escape"]]),
    "string.regex": (["regexp"], [["string.regexp"]]),
    "string.special": ([], [["string.quoted.other"]]),
    "string.special.symbol": ([], [["constant.other.symbol"]]),
    "tag": ([], [["entity.name.tag"]]),
    "tag.doctype": ([], [["meta.tag.metadata.doctype", "entity.name.tag"]]),
    "text.literal": ([], [["markup.inline.raw"]]),
    "title": ([], [["markup.heading.markdown", "entity.name.section.markdown"]]),
    "type": (["type", "class"], [["entity.name.type"]]),
    "type.builtin": (["type.defaultLibrary", "type"], [["support.type.primitive"]]),
    "type.interface": (["interface", "type"], [["entity.name.type.interface"]]),
    "type.parameter": (["typeParameter", "type"], [["entity.name.type.parameter"]]),
    "variable": (["variable"], [["variable.other.readwrite"]]),
    "variable.member": (["property"], [["variable.other.property"]]),
    "variable.parameter": (["parameter"], [["variable.parameter"]]),
    "variable.special": ([], [["variable.language.this"]]),
    "variant": (["enumMember"], [["variable.other.enummember"]]),
}


def color(value):
    """Normalize a VS Code color to #rrggbb or #rrggbbaa (or None)."""
    if not value or not isinstance(value, str) or not value.startswith("#"):
        return None
    hex_ = value[1:].lower()
    if len(hex_) in (3, 4):
        hex_ = "".join(c * 2 for c in hex_)
    if len(hex_) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", hex_):
        return None
    return "#" + hex_


def with_alpha(value, alpha):
    value = color(value)
    if value is None:
        return None
    return value[:7] + alpha

def _lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _gam(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def rgb(value, over=None):
    """#rrggbb[aa] -> linear RGB floats, compositing alpha over `over` if given."""
    value = color(value)
    channels = [_lin(int(value[i : i + 2], 16) / 255) for i in (1, 3, 5)]
    if len(value) == 9 and over:
        alpha = int(value[7:9], 16) / 255
        under = rgb(over)
        channels = [_lin(_gam(c) * alpha + _gam(u) * (1 - alpha)) for c, u in zip(channels, under)]
    return channels


def hex_(channels):
    return "#" + "".join(f"{round(min(1, max(0, _gam(c))) * 255):02x}" for c in channels)


def to_oklab(channels):
    r, g, b = channels
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def from_oklab(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ]


def in_gamut(channels):
    return all(-1e-4 <= c <= 1 + 1e-4 for c in channels)


def oklab_hex(L, a, b):
    """OKLab -> hex, pulling chroma in until the color fits sRGB."""
    if in_gamut(from_oklab((L, a, b))):
        return hex_(from_oklab((L, a, b)))
    lo, hi = 0.0, 1.0  # largest chroma scale that still fits
    for _ in range(24):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if in_gamut(from_oklab((L, a * mid, b * mid))) else (lo, mid)
    return hex_(from_oklab((L, a * lo, b * lo)))


def luminance(value, over=None):
    r, g, b = rgb(value, over)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = luminance(fg, bg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def shift(value, dl, over=None):
    """Move a color's OKLab lightness by dl, keeping hue and chroma."""
    L, a, b = to_oklab(rgb(value, over))
    return oklab_hex(max(0, min(1, L + dl)), a, b)


def mix(a, b, t, over=None):
    """Blend a toward b by t in OKLab."""
    la, lb = to_oklab(rgb(a, over)), to_oklab(rgb(b, over))
    return oklab_hex(*(x + (y - x) * t for x, y in zip(la, lb)))


def lift_to(value, bg, target, chroma=1.0):
    """Scale chroma, then raise lightness until value reaches `target` contrast on bg."""
    L, a, b = to_oklab(rgb(value, bg))
    a, b = a * chroma, b * chroma
    out = oklab_hex(L, a, b)
    while contrast(out, bg) < target and L < 1:
        L += 0.005
        out = oklab_hex(L, a, b)
    return out


def at_contrast(bg, target, toward):
    """Walk from bg toward `toward` until the result hits `target` contrast on bg."""
    for step in range(1, 201):
        out = mix(bg, toward, step / 200)
        if contrast(out, bg) >= target:
            return out
    return toward


def scope_matches(part, scope):
    return scope == part or scope.startswith(part + ".")


def selector_score(selector, stack):
    """Score a TextMate selector against a scope stack, or None if no match."""
    parts = selector.split()
    if not parts or not scope_matches(parts[-1], stack[-1]):
        return None
    i = len(stack) - 2
    for part in reversed(parts[:-1]):
        while i >= 0 and not scope_matches(part, stack[i]):
            i -= 1
        if i < 0:
            return None
        i -= 1
    return (parts[-1].count(".") + 1, len(parts))


def textmate_color(rules, stack):
    best = None
    for index, (selector, foreground) in enumerate(rules):
        score = selector_score(selector, stack)
        if score is not None and (best is None or (*score, index) >= best[0]):
            best = ((*score, index), foreground)
    return best[1] if best else None


def textmate_rules(theme):
    rules = []
    for rule in theme.get("tokenColors", []):
        foreground = color(rule.get("settings", {}).get("foreground"))
        if not foreground or "scope" not in rule:
            continue
        scopes = rule["scope"]
        if isinstance(scopes, str):
            scopes = scopes.split(",")
        rules.extend((s.strip(), foreground) for s in scopes if s.strip())
    return rules


def semantic_color(theme, keys):
    if not theme.get("semanticHighlighting", False):
        return None
    tokens = theme.get("semanticTokenColors", {})
    for key in keys:
        value = tokens.get(key)
        if isinstance(value, dict):
            value = value.get("foreground")
        if color(value):
            return color(value)
    return None


def build_syntax(theme):
    c = theme["colors"]
    fallback = color(c.get("editor.foreground"))
    rules = textmate_rules(theme)
    syntax = {}
    for name, (semantic_keys, stacks) in SYNTAX.items():
        value = semantic_color(theme, semantic_keys)
        for stack in stacks:
            if value:
                break
            value = textmate_color(rules, stack)
        if name == "hint":
            value = color(c.get("editorInlayHint.foreground"))
        elif name == "predictive":
            value = color(c.get("editorGhostText.foreground"))
        syntax[name] = {
            "color": value or fallback,
            "font_style": "normal",
            "font_weight": 400,
        }
    return syntax


def muted_comment(bg):
    """Neutral gray tinted by the editor background, so comments stay out of the way."""
    r, g, b = (int(bg[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(v + (255 - v) * COMMENT_LIFT):02x}" for v in (r, g, b))


def build_style(theme, palette=None):
    palette = palette or {}
    c = {k: color(v) for k, v in theme["colors"].items()}
    g = lambda *keys: next((c[k] for k in keys if c.get(k)), None)

    editor_bg = g("editor.background")
    shell_bg = g("titleBar.activeBackground", "sideBar.background", "editor.background")
    divider = g("editorGroup.border", "panel.border") or TRANSPARENT
    accent = g("focusBorder", "button.background")
    hover = g("list.hoverBackground")
    selected = g("list.activeSelectionBackground")

    style = {
        "background.appearance": "opaque",
        "background": shell_bg,
        "border": divider,
        "border.variant": divider,
        "border.focused": TRANSPARENT,
        "border.selected": TRANSPARENT,
        "border.transparent": TRANSPARENT,
        "border.disabled": divider,
        "elevated_surface.background": g("editorWidget.background", "menu.background"),
        "surface.background": g("sideBar.background"),
        "element.background": g("input.background"),
        "element.hover": hover,
        "element.active": selected,
        "element.selected": selected,
        "element.disabled": g("input.background"),
        "drop_target.background": with_alpha(accent, "33"),
        "ghost_element.background": TRANSPARENT,
        "ghost_element.hover": hover,
        "ghost_element.active": selected,
        "ghost_element.selected": selected,
        "ghost_element.disabled": TRANSPARENT,
        "text": g("foreground"),
        "text.muted": g("descriptionForeground"),
        "text.placeholder": g("input.placeholderForeground", "descriptionForeground"),
        "text.disabled": g("disabledForeground"),
        "text.accent": g("textLink.foreground", "focusBorder"),
        "icon": g("icon.foreground", "foreground"),
        "icon.muted": g("activityBar.inactiveForeground", "descriptionForeground"),
        "icon.disabled": g("disabledForeground"),
        "icon.placeholder": g("descriptionForeground"),
        "icon.accent": accent,
        "status_bar.background": g("statusBar.background"),
        "title_bar.background": g("titleBar.activeBackground"),
        "title_bar.inactive_background": g("titleBar.inactiveBackground", "titleBar.activeBackground"),
        "toolbar.background": editor_bg,
        "tab_bar.background": g("editorGroupHeader.tabsBackground"),
        "tab.inactive_background": g("tab.inactiveBackground"),
        "tab.active_background": g("tab.activeBackground"),
        "search.match_background": g("editor.findMatchHighlightBackground"),
        "search.active_match_background": g("editor.findMatchBackground"),
        "panel.background": g("sideBar.background"),
        "panel.focused_border": accent,
        "panel.indent_guide": g("tree.indentGuidesStroke", "editorIndentGuide.background1"),
        "panel.indent_guide_active": g("editorIndentGuide.activeBackground1"),
        "panel.indent_guide_hover": g("editorIndentGuide.activeBackground1"),
        "pane.focused_border": TRANSPARENT,
        "pane_group.border": divider,
        "scrollbar.thumb.background": g("scrollbarSlider.background"),
        "scrollbar.thumb.hover_background": g("scrollbarSlider.hoverBackground"),
        "scrollbar.thumb.active_background": g("scrollbarSlider.activeBackground"),
        "scrollbar.thumb.border": TRANSPARENT,
        "scrollbar.track.background": TRANSPARENT,
        "scrollbar.track.border": TRANSPARENT,
        "minimap.thumb.background": g("minimapSlider.background"),
        "minimap.thumb.hover_background": g("minimapSlider.hoverBackground"),
        "minimap.thumb.active_background": g("minimapSlider.activeBackground"),
        "minimap.thumb.border": TRANSPARENT,
        "editor.foreground": g("editor.foreground"),
        "editor.background": editor_bg,
        "editor.gutter.background": g("editorGutter.background", "editor.background"),
        "editor.subheader.background": g("editorWidget.background"),
        "editor.active_line.background": g("editor.lineHighlightBackground"),
        "editor.highlighted_line.background": g("editor.lineHighlightBackground"),
        "editor.line_number": g("editorLineNumber.foreground"),
        "editor.active_line_number": g("editorLineNumber.activeForeground"),
        "editor.hover_line_number": g("editorLineNumber.activeForeground"),
        "editor.invisible": g("editorWhitespace.foreground"),
        "editor.wrap_guide": g("editorRuler.foreground"),
        "editor.active_wrap_guide": g("editorRuler.foreground"),
        "editor.indent_guide": g("editorIndentGuide.background1"),
        "editor.indent_guide_active": g("editorIndentGuide.activeBackground1"),
        "editor.document_highlight.read_background": g("editor.wordHighlightBackground"),
        "editor.document_highlight.write_background": g("editor.wordHighlightStrongBackground"),
        "editor.document_highlight.bracket_background": g("editorBracketMatch.background"),
        "link_text.hover": g("textLink.activeForeground"),
        "terminal.background": g("terminal.background", "editor.background"),
        "terminal.foreground": g("terminal.foreground", "editor.foreground"),
        "terminal.ansi.background": g("terminal.background", "editor.background"),
        "terminal.bright_foreground": g("terminal.ansiBrightWhite"),
        "terminal.dim_foreground": g("descriptionForeground"),
    }

    for name in ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"):
        title = name.capitalize()
        style[f"terminal.ansi.{name}"] = g(f"terminal.ansi{title}")
        style[f"terminal.ansi.bright_{name}"] = g(f"terminal.ansiBright{title}")

    statuses = {
        "error": g("editorError.foreground", "errorForeground"),
        "warning": g("editorWarning.foreground"),
        "info": g("editorInfo.foreground"),
        "hint": g("editorInlayHint.foreground", "editorHint.foreground"),
        "success": g("gitDecoration.addedResourceForeground"),
        "created": g("gitDecoration.untrackedResourceForeground", "gitDecoration.addedResourceForeground"),
        "modified": g("gitDecoration.modifiedResourceForeground"),
        "deleted": g("gitDecoration.deletedResourceForeground"),
        "renamed": g("gitDecoration.renamedResourceForeground"),
        "conflict": g("gitDecoration.conflictingResourceForeground"),
        "ignored": g("gitDecoration.ignoredResourceForeground"),
        "hidden": g("disabledForeground"),
        "unreachable": g("editorUnnecessaryCode.border", "descriptionForeground"),
        "predictive": g("editorGhostText.foreground"),
    }
    for name, value in statuses.items():
        if not value:
            continue
        style[name] = value
        style[f"{name}.background"] = with_alpha(value, "1a")
        style[f"{name}.border"] = with_alpha(value, "66")
    style["hint.background"] = g("editorInlayHint.background") or style.get("hint.background")

    style["version_control.added"] = g("editorGutter.addedBackground")
    style["version_control.modified"] = g("editorGutter.modifiedBackground")
    style["version_control.deleted"] = g("editorGutter.deletedBackground")
    style["version_control.renamed"] = g("gitDecoration.renamedResourceForeground")
    style["version_control.conflict"] = g("gitDecoration.conflictingResourceForeground")
    style["version_control.ignored"] = g("gitDecoration.ignoredResourceForeground")

    selection = g("editor.selectionBackground")
    cursor = "#ffffff"
    player_colors = [accent] + [
        g(f"terminal.ansi{n}") for n in ("Blue", "Magenta", "Yellow", "Green", "Cyan", "Red")
    ]
    style["players"] = [
        {
            "cursor": cursor if i == 0 else p,
            "background": p,
            "selection": selection if i == 0 else with_alpha(p, "3d"),
        }
        for i, p in enumerate(player_colors)
        if p
    ]
    style["accents"] = [
        c[f"editorBracketHighlight.foreground{i}"]
        for i in range(1, 7)
        if c.get(f"editorBracketHighlight.foreground{i}")
    ]
    style["syntax"] = build_syntax(theme)
    muted = muted_comment(editor_bg)
    for key in ("comment", "comment.doc"):
        if key in style["syntax"]:
            style["syntax"][key]["color"] = muted

    style = {k: v for k, v in style.items() if v is not None}
    for key, value in palette.get("syntax", {}).items():
        style["syntax"][key]["color"] = value
    style.update(palette.get("style", {}))  # direct Zed keys with no VS Code equivalent
    return sharpen(style, palette)


def sharpen(style, palette=None):
    """Add depth and hierarchy: darker shell, crisp dividers, tinted selection,
    quieter gutter, richer syntax. Every text color is held to a contrast floor."""
    palette = palette or {}
    edge = palette.get("edge", {})
    chroma = palette.get("chroma", EDGE_CHROMA)
    editor_bg = style["editor.background"]
    text = style["editor.foreground"]

    # Shell sits a step below the editor so the code surface reads as raised.
    shell_L = to_oklab(rgb(style["background"]))[0]
    editor_L = to_oklab(rgb(editor_bg))[0]
    drop = edge.get("shell_drop", EDGE_SHELL_DROP)
    target_L = min(shell_L, max(edge.get("shell_floor", EDGE_SHELL_FLOOR), editor_L - drop))
    shell = shift(style["background"], target_L - shell_L)
    if editor_L - target_L < drop:  # shell already at the floor: lift the editor
        editor_bg = shift(editor_bg, drop - (editor_L - target_L))
    for key in ("background", "panel.background", "surface.background", "status_bar.background",
                "title_bar.background", "title_bar.inactive_background", "tab_bar.background",
                "tab.inactive_background"):
        style[key] = shell
    for key in ("editor.background", "editor.gutter.background", "toolbar.background",
                "tab.active_background", "terminal.background", "terminal.ansi.background"):
        style[key] = editor_bg

    # Dividers you can actually see, still quiet.
    border = at_contrast(shell, edge.get("border", EDGE_BORDER), text)
    for key in ("border", "border.disabled", "pane_group.border"):
        style[key] = border
    style["border.variant"] = at_contrast(shell, edge.get("border_variant", EDGE_BORDER_VARIANT), text)

    # UI text hierarchy.
    style["text"] = lift_to(style["text"], shell, EDGE_TEXT_MIN)
    style["text.muted"] = lift_to(style["text.muted"], shell, EDGE_TEXT_MUTED_MIN)
    style["text.placeholder"] = lift_to(style["text.placeholder"], shell, EDGE_TEXT_MUTED_MIN)
    if "text.accent" in style:
        style["text.accent"] = lift_to(style["text.accent"], shell, EDGE_TEXT_MUTED_MIN)

    # Rows: hover lifts, selection carries the accent.
    accent = style.get("icon.accent", text)
    hover = shift(shell, edge.get("hover_lift", EDGE_HOVER_LIFT))
    selected = mix(shift(shell, edge.get("selected_lift", EDGE_HOVER_LIFT)), accent, edge.get("selected_tint", EDGE_SELECTED_TINT))
    while contrast(style["text"], selected) < edge.get("selected_text", EDGE_SELECTED_TEXT):  # labels stay crisp on it
        selected = shift(selected, -0.005)
    for prefix in ("element", "ghost_element"):
        style[f"{prefix}.hover"] = hover
        style[f"{prefix}.active"] = selected
        style[f"{prefix}.selected"] = selected

    # Gutter recedes; the active line number and the code lead.
    style["editor.line_number"] = at_contrast(editor_bg, EDGE_LINE_NUMBER, text)
    style["editor.active_line_number"] = text
    style["editor.hover_line_number"] = text

    # Gutter diff bars: optionally blended toward the editor so they hint, not shout.
    if "vc_strength" in edge:
        for kind in ("added", "modified", "deleted", "renamed", "conflict"):
            key = f"version_control.{kind}"
            if key in style:
                style[key] = blend(style[key][:7], edge["vc_strength"], editor_bg[:7])  # like opacity

    # Syntax: a touch more saturation, never below the readability floor.
    for key, entry in style["syntax"].items():
        if key in ("comment", "comment.doc"):
            entry["color"] = lift_to(entry["color"], editor_bg, EDGE_COMMENT)
        elif key not in ("hint", "predictive"):
            entry["color"] = lift_to(entry["color"], editor_bg, EDGE_SYNTAX_MIN, chroma)

    return style


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    files = sorted(source.glob("*-color-theme.json"))
    if not files:
        sys.exit(f"No *-color-theme.json files found in {source}")

    OUT.mkdir(exist_ok=True)
    for path in files:
        palette = path.name.removesuffix("-color-theme.json")
        if palette.endswith(SKIP_SUFFIXES) or palette in SKIP_PALETTES or palette not in ONLY_PALETTES:
            continue
        for config in [PALETTES.get(palette, {}), *VARIANTS.get(palette, [])]:
            theme = remap_colors(json.loads(path.read_text()), config.get("remap", {}))
            theme["colors"].update(config.get("colors", {}))
            theme["name"] = config.get("name", theme["name"])
            write(config.get("file", palette), theme, config)



def unbordered_style(style):
    """Return a seamless, borderless variant of a built Zed style."""
    ed = style["editor.background"]
    elev = style.get("elevated_surface.background", ed)
    out = dict(style)
    for k in (
        "background", "panel.background", "surface.background",
        "status_bar.background", "title_bar.background", "title_bar.inactive_background",
        "tab_bar.background", "tab.inactive_background", "toolbar.background",
    ):
        out[k] = ed
    out["tab.active_background"] = elev
    for k in (
        "border", "border.variant", "border.disabled", "border.focused",
        "border.selected", "pane_group.border", "pane.focused_border", "panel.focused_border",
    ):
        out[k] = TRANSPARENT
    return out


def write(palette, theme, config):
    style = build_style(theme, config)
    appearance = theme.get("type", "dark")
    themes = [
        {"name": theme["name"], "appearance": appearance, "style": style},
        {"name": f"{theme['name']} Unbordered", "appearance": appearance, "style": unbordered_style(style)},
    ]
    family = {"$schema": SCHEMA, "name": theme["name"], "author": AUTHOR, "themes": themes}
    (OUT / f"{palette}.json").write_text(json.dumps(family, indent=2, ensure_ascii=False) + "\n")
    for t in themes:
        print(f"themes/{palette}.json: {t['name']}")


if __name__ == "__main__":
    main()

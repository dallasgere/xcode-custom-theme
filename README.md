# Dallas Xcode Theme

Xcode theme for zed with some changes to git status colors and slightly altered syntax highlighting to make it more appealing for me

## Install locally

1. In Zed, open the command palette and run `zed: install dev extension`.
2. Select this folder.
3. Run `theme selector: toggle` and pick `Xcode`.

Or copy the files in `themes/` into `~/.config/zed/themes/`.

## Regenerating

The themes are generated from the Xcode VS Code theme in the [Vine](https://github.com/dallasgere/vine) repo:

```bash
python3 scripts/build.py ../vine/themes
```

The script maps each VS Code workbench color to its closest Zed equivalent, then pins syntax colors to Xcode's own palette (the `XCODE` table at the top of the script). Source control colors use VS Code's roles (added, changed, deleted) in Xcode's shades.

## Differences from Xcode

- Zed highlights with tree-sitter, which can't tell project symbols from SDK ones, so each syntax role gets one Xcode color.
- Zed theme syntax styles cannot underline or strike through text, so invalid and deprecated code use color only.

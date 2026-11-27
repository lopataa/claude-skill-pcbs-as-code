# building-pcbs-as-code

A Claude Code skill for PCBs that are generated from Python with KiCad's
`pcbnew` API and `kicad-cli`, with no GUI editing. It ships with `kicadgen`,
the shared library the skill tells the agent to reuse.

## Install

Paste this into your coding agent (Claude Code, Codex, Antigravity, or any
other agent that reads `SKILL.md` skills):

```text
Install the agent skill "building-pcbs-as-code" from
https://github.com/lopataa/claude-skill-pcbs-as-code at user level, so it
works in every project.

1. Clone it over HTTPS into ~/.agents/skills/building-pcbs-as-code (create
   ~/.agents/skills if it's missing). Keep the folder name
   building-pcbs-as-code: it must match `name:` in SKILL.md. If the folder
   already exists, run `git pull` in it instead.
2. Make sure you will load it. Use the user-level skills folder your own docs
   give; check them if you're not sure. Known ones:
   - Codex: ~/.agents/skills, so nothing more to do. (Older Codex versions
     read ~/.codex/skills; link it there too if it doesn't show up.)
   - Claude Code: ~/.claude/skills
   - Antigravity (app, IDE and agy CLI): ~/.gemini/config/skills
   If yours is another folder, symlink <that folder>/building-pcbs-as-code to
   the clone. On Windows, or if you don't follow symlinks, clone straight into
   your own folder instead of ~/.agents/skills.
3. Read SKILL.md and README.md. Check whether KiCad, kicad-cli and KiCad's
   bundled Python are installed, and report the KiCad version and the Python
   path.
4. Summarise in a few lines what the skill covers, and tell me whether I need
   to restart you or start a new session before it shows up.

Don't change any of my projects.
```

To update it later, ask: "Update the building-pcbs-as-code skill (git pull
where you installed it) and tell me what changed."

## What is in here

| Path | What |
| --- | --- |
| `SKILL.md` | The skill: the workflow, the rules, and the checks that gate a build |
| `reference.md` | Command reference: kicad-cli, the build steps, JLC outputs |
| `nfc_match_model.py` | Matching-network model for a 13.56 MHz reader antenna |
| `kicadgen/` | The shared library: schematic generation (`sch`), board utilities (`pcbutil`), the router (`router`, `routing`), silkscreen tidying (`tidy_silk`), JLC BOM/CPL (`jlc`), and the command-line tools `check_place`, `dump_geom`, `plot_zoom` and `gen_logo` |

Run everything with KiCad's bundled Python. A board's scripts live in a folder
next to `kicadgen/`, put `..` on `sys.path` and import it as
`from kicadgen import sch`. To use it in a new project, copy `kicadgen/` next to
the board folders.

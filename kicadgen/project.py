"""Find a board's own files from the directory a tool runs in.

The shared command-line tools in this package (check_place, dump_geom,
gen_logo) used to be copied into every board folder with the board's name
written in. They now take the board on the command line, or find it here:
a board folder holds exactly one .kicad_pro, and its name is the project
name. That is NOT always the folder name (a folder board-revC can hold
board.kicad_pro), so never derive it from the folder.
"""
import glob
import os
import sys


def name(where='.'):
    """The project name: the stem of the one .kicad_pro in `where`."""
    hits = sorted(glob.glob(os.path.join(where, '*.kicad_pro')))
    if len(hits) != 1:
        sys.exit('expected one .kicad_pro in %s, found %d; pass the board explicitly'
                 % (os.path.abspath(where), len(hits)))
    return os.path.splitext(os.path.basename(hits[0]))[0]


def board(where='.'):
    """Path of the project's .kicad_pcb."""
    return os.path.join(where, name(where) + '.kicad_pcb')


def pretty(where='.'):
    """Path of the project's own footprint library."""
    return os.path.join(where, name(where) + '.pretty')

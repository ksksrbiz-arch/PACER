"""Command-line subcommands grouped by business area.

Each module here registers a Click subgroup against the root ``cli`` defined
in :mod:`pacer.main`. Import the module once at ``main`` bootstrap to attach
its group — keeps ``main.py`` from ballooning.
"""

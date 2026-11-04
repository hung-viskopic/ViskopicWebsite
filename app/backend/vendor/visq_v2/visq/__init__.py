"""Visq: the Viskopic viva system. Visq 1 = AROA-built questions + answer-driven follow-ups. Visq 2 = + the world-tree trial path (clarify, extension, short viva) in the web portal."""
VERSION = 2            # the Visq series follows 1, 2, 3, 5, 7, 11 ... (see README)
PROMPT_VERSION = "q1.20"       # q1.9: reviewer accepts hypotheticals for limit/case; cases vary what is constrained (see CHANGELOG in README)
EXTRACTION_VERSION = "e1"       # bump only if the extraction prompt changes (it keys the cache)
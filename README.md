# Nightbook

Bitget rTokens trade 24/7. The underlying real stock does not, cash market
hours are 09:30-16:00 ET. Outside those hours, rTokens trade on Bitget's own
internal order book with no live underlying market to anchor them. That gap
is the real structural opening this project measures.

This is not a hunch. It is a synthesis of two other public builds for the
same hackathon: **Rift24** showed the overnight rToken price move does not
predict the next cash open (direction is close to random overnight).
**Custos** showed rToken order book depth collapses on weekends independent
of the underlying company's fundamentals. Put together: don't try to predict
direction, check whether being the counterparty during those thin, wide
hours is worth doing instead.

## What this actually is

A live decision agent, not a backtest sitting in a README. Every 5 minutes,
GitHub Actions runs [`decide.py`](decide.py), which:

1. Pulls the real live order book for 15 liquid rTokens from Bitget's public
   API (no credentials needed for market data).
2. Appends the snapshot to [`data/history.csv`](data/history.csv), the same
   growing dataset used throughout validation.
3. Recomputes, for every symbol, whether resting a passive quote inside the
   spread would have been profitable after a real, confirmed 20bps
   round-trip fee (10bps maker/taker, no maker rebate, verified live
   against Bitget's fee schedule), checked at 4 different quote depths so a
   result has to be robust, not a lucky parameter choice.
4. Writes a decision per symbol to [`decisions/latest.md`](decisions/latest.md):
   **QUOTE** only if the edge is positive at every depth tested with at
   least 20 fills backing each one, **STAND_DOWN** otherwise, with the real
   reason (insufficient sample, or edge not robust, or a known scheduled
   event is close, or it's regular market hours and the thesis doesn't
   apply there).
5. Commits the updated history and decision log back to this repo. The git
   history of this repo *is* the audit trail.

## What this is not

No real orders. No exchange API keys or credentials anywhere in this repo.
No capital at risk. A `QUOTE` decision means "the validated methodology
currently says this symbol clears the bar," a paper decision, not a live
trade. Every competing build we found for this hackathon (Rift24, Custos,
factor-atlas) also ran paper-only, this isn't a corner cut for the demo,
it's the honest scope.

## Honest current state

As of first deploy, every symbol in the universe is `STAND_DOWN`, either
because the sample size is still too small to trust (most symbols need more
fills before any decision means anything) or because the edge that did show
up in small samples did not survive being checked across all 4 quote
depths. Two names (RAMD, RMETA) looked positive with 3-4 fills during
research and flipped negative once the sample grew past 40 fills, which is
exactly why this agent requires 20+ fills at every depth before it will
ever say `QUOTE`, not because 20 is a magic number, but because we watched
smaller samples lie twice already.

This might keep saying `STAND_DOWN` for the whole window. That is a valid,
honest outcome, not a failure of the build, see Rift24's own result for
precedent: they stood down on effectively every opportunity and reported it
plainly rather than dressing up a null result.

## Methodology detail

See [`decide.py`](decide.py)'s module docstring and
[`event_calendar.py`](event_calendar.py) for the exact rules. Fill detection
is a conservative snapshot-crossing proxy (a quote only counts as filled if
the *next* snapshot's NBBO fully crossed through our price), this
undercounts real fills rather than flattering the result. Sample size,
n, is printed next to every claim, nothing here is presented without it.

## Where the deeper research lives

The original validation kit (finer-grained 60s local collection, the
research that established this methodology before it was deployed here) is
at `bitget-s2/research/` in the parent project, not part of this repo.

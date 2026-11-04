# The road to 1.0

What is left before 1.0 and the public launch (a Reddit post). The aim is an app
polished enough that nobody can fairly call it "AI slop": rules that are right,
a first ten minutes with no rough edges, and a repo that is honest about how it
was made. Agreed on 2026-11-04.

Take an item off by doing it, and delete it in the same commit. The longer
engine debts live in [the debts file](debts.md); this file only orders them.

## Before launch

### Deploy the relay fix

`fix/pre-launch` makes the hub retry for good (`SessionHub._repair`) and makes the
certificate-renewal hook restart `mm-sessions` too (see [the session
notes](session.md)). Neither reaches the VPS by itself:

- **The hook** was installed on the box by hand on 2026-11-04, so the 0.9.4 hub
  already running is covered for the next renewal. Careful: `deploy/deploy.sh`
  reinstalls the hook from the box's `main` checkout, so running it before this
  branch reaches `main` puts the old hook back.
- **The hub code** goes out with the next release and redeploy.

### Conditions remember how they come off (#20b)

When an Affliction puts on Stunned, End turn offers it the Stamina recovery check
that belongs to *damage-caused* Stunned. Impressions the GM's table tracks
(`ImpressController._lingering`) are forgotten when the GM's app restarts. Both are
fixed by one per-condition record on `AppliedCondition`, written by
`targeting.apply_rung`, saying how that condition is shaken off. Probably a protocol
bump (the user is fine with that). Details are in [the debts file](debts.md#end-of-turn-does-one-thing-so-far).
Rules mistakes are what M&M players will spot first.

### Combat maneuvers

All-out Attack, Defensive Attack, Power Attack, Accurate Attack and the rest, on the
sheet, trading bonuses as the rules allow. They come up every round at a real table,
and missing them is the gap players will notice most. Listed in the README's
"Future plans".

### Screenshots in the README

The README has no images at all. Add one GIF of the Power Constructor (dragging an
effect and an extra in, the cost and stat block updating), plus a sheet and a GM
window. Use a good-looking sample character. The `run-mm-companion` skill can take
the stills. Also trim the Features section, because Reddit readers skim.

### A new-user pass

Install the release on a clean machine (or a fresh Windows user) and act as a
stranger: create a character, build a power, buy gear, join a session, run one as
GM. Fix whatever is confusing in the first ten minutes: empty states, wording,
error messages, dialogs that open behind the sheet. If possible, run one real
session with people who have never seen the app.

### Check the server's capacity

Every hosted session runs on one Timeweb VPS, with a cap of
`DEFAULT_MAX_SESSIONS = 50` in `server/hub.py`. Before a traffic spike, find out
what the box can actually hold, and what a new GM sees when the cap is reached
(it is a readable "this server is holding its limit" error).

### Make it read as a 1.0

- The README still says "pre-alpha" and "Expect breaking changes".
- 1.0 is a promise that saved characters keep loading. Confirm the character
  file format is one we are willing to migrate forward from, rather than break.
- **Be open about the AI assistance.** Most commits carry `Co-Authored-By:
  Claude`, and `CLAUDE.md` and `.claude/skills/` are in the repo. Say so in the
  post rather than have someone "discover" it. Being caught hiding it goes over
  far worse on RPG subreddits than admitting it.

### Sign the Windows installer (optional)

The installer is unsigned, so SmartScreen warns on first run. The README now says
so. Signing would remove the warning: SignPath offers free signing to open-source
projects, and Azure Trusted Signing is a few dollars a month.

## After 1.0

The roadmap features in the README:

- **Ranks & Measures** — the measurement table as a tool. Small and self-contained;
  a candidate to pull forward if there is time.
- **Summoned minions** — a Summon's or a Minion advantage's build sent to the GM
  window as a creature (`Character.summoned` already exists).
- **GM actions** — acting for NPCs, applying effects to several creatures at once.
- **GM request help** — a player asks the GM for a ruling from the session.

And the engine debts, all in [the debts file](debts.md):

- The rest of the minion rules: Takedown's free attack (#21), Menaces, Assess
  against a group (#21), a minion's 20 against a minion, request-mode Impress DCs.
- The rest of End turn: Affliction overcome checks, Suffocation, a turn tracker.
- The rest of area attacks: a Hero Point inside the table, area Nullify and Move
  Object, Selective / cover / the size rule.
- Restating the Powers and Equipment cards instead of rebuilding them (#19). It
  is not visible today.
- Lifting the PySide6 pin (#22). Blocked until PySide6 6.12.1 is out.

Smaller things raised along the way:

- Double-clicking a divider snaps the pane to its recommended width, which is easy
  to trigger by accident.
- An even shorter join code (6–8 characters) through a lookup on the hub. It was
  designed but not built.
- Updating in place on Linux (it opens the release page today), and a macOS build
  (it runs from source only).

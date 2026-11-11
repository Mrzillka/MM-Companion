# Privacy policy

*Last updated: 2026-11-11*

MM-Companion is a desktop app. It has no accounts, no telemetry and no analytics,
and it does not send your characters, notes or files anywhere unless you play
online. This page lists every time the app connects to another computer, and what
is sent when it does.

## What stays on your computer

Your characters, notes, images, settings and NPCs are saved in your workspace
folder on your own computer. So are the app's logs (`logs/errors.log`, crash logs
and installer logs). The app never uploads its logs. If you report a bug, you
decide what to attach.

## The update check

When MM-Companion starts, it asks GitHub once whether a newer version has been
released. This is one request to GitHub's public "latest release" address for this
project. Like every request, it reveals your IP address to GitHub, and it names the
app and its version (`MM-Companion/<version>`). Nothing about you or your
characters is sent.

Nothing is downloaded unless you click **Update**. The app then downloads the
installer from GitHub and checks it against the size and SHA-256 fingerprint
GitHub publishes for it before running it. On a copy the app cannot update in
place, **Update** opens the release page in your browser instead.

GitHub's handling of these requests is covered by the
[GitHub General Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).

**To turn the check off:** Settings ▸ Preferences ▸ General (or **Settings** on the
launcher), untick **Check for updates when MM-Companion starts**, and save.

## Playing online (GM Mode and Join Session)

Nothing goes online until you use GM Mode or Join Session. The app ships pointing at a
public session server that the project runs. You can change that address, or clear
it to host on your own computer instead.

**What the session server receives**

- Your **IP address**, as any internet server does when you connect to it.
- What you choose to share at the table: your display name, the character you
  bring (its game stats and a small thumbnail of its portrait), your rolls and the
  notes and requests in the shared history, and what the GM puts on the shared
  scene (names, visible conditions and small portraits). Your app also sends its
  version and a fingerprint of which mods are enabled, so mismatched apps can be
  warned.
- The GM's NPC sheets stay on the GM's computer and are not sent.

The connection is encrypted (TLS) between your app and the server. The server
itself can read what it receives, because it has to in order to run the game.

**What it stores, and for how long**

The server keeps each session on disk so that a game continues when the GM is
away: the session's name, the players' display names and characters, and the roll
history (including the GM's hidden rolls, which players never see). A session is
deleted when its GM deletes it, or automatically after **30 days** with no one
joining, rolling or renaming it. A GM can remove a player from a session, which
removes that player's card.

The server's activity log records events such as a session being created,
deleted or cleaned up, with the session's name or id. It does not record
players' IP addresses. If a GM hosts from their own computer and routes through
the server's relay, the log records that GM's IP address when the session
registers.

No data is sold or shared with anyone, and nothing is used for advertising.

**Hosting it yourself**

If you host on your own computer, players connect to you directly. The join code
then contains your public IP address, so share it only with your players. If your
router allows it, the app asks the router (through UPnP, on your local network) to
forward the session's port. If you use a tunnel service, that service's own
privacy policy applies.

You can also run your own session server and relay; see the
[networking guide](docs/mm-session-networking.md). Then the project's server is
not involved at all.

## Links

Links you click in your notes, and the release page from the update check, open
in your web browser. What happens there is up to that website.

## Questions

Open an issue on the project's
[GitHub issue tracker](https://github.com/Mrzillka/MM-Companion/issues). If you
want a session on the public server deleted and can no longer reach it from your
app, ask there too (do not post your join code or GM token in public).

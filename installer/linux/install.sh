#!/bin/sh
# Install, upgrade or remove MM-Companion on Linux.
#
# Ships at the top of the release tarball, beside the frozen app, and is copied
# into the install so it can remove it again later. See docs/packaging.md.
#
#   ./install.sh                     install (or upgrade) for the current user
#   sudo ./install.sh --system       install (or upgrade) for every user
#   ./install.sh --uninstall         remove it again (add --system if it was)
#   ./install.sh --uninstall --purge also delete your characters, mods, settings
#
# User data never lives in the install: it is in the per-user workspace,
# ${XDG_DATA_HOME:-~/.local/share}/MM-Companion, so an upgrade cannot touch it.
set -eu

APP_ID=mm-companion
EXE=MM-Companion

usage() {
    sed -n '/^#   /s/^#   //p' "$0"
}

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

mode=user
action=install
purge=0
while [ $# -gt 0 ]; do
    case $1 in
        --system) mode=system ;;
        --uninstall | --remove) action=uninstall ;;
        --purge) purge=1 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

if [ "$mode" = system ]; then
    [ "$(id -u)" -eq 0 ] || die "--system needs root: sudo $0 --system"
    APP_DIR=/opt/$APP_ID
    BIN_DIR=/usr/local/bin
    SHARE_DIR=/usr/local/share
else
    # Under sudo this would install into root's home, where no one finds it.
    [ "$(id -u)" -ne 0 ] || die "run without sudo for a per-user install, or pass --system"
    SHARE_DIR=${XDG_DATA_HOME:-$HOME/.local/share}
    APP_DIR=$HOME/.local/opt/$APP_ID
    BIN_DIR=$HOME/.local/bin
fi
APPS_DIR=$SHARE_DIR/applications
ICON_DIR=$SHARE_DIR/icons/hicolor/256x256/apps
DESKTOP_FILE=$APPS_DIR/$APP_ID.desktop
LAUNCHER=$BIN_DIR/$APP_ID
WORKSPACE=${XDG_DATA_HOME:-$HOME/.local/share}/MM-Companion

# The menus and icon theme cache are refreshed if the tools exist; a desktop
# without them rescans on its own.
refresh_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t "$SHARE_DIR/icons/hicolor" >/dev/null 2>&1 || true
    fi
}

uninstall() {
    if [ -d "$APP_DIR" ] || [ -e "$DESKTOP_FILE" ] || [ -e "$LAUNCHER" ]; then
        rm -rf "$APP_DIR"
        rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_DIR/$APP_ID.png"
        refresh_caches
        say "Removed MM-Companion from $APP_DIR."
    elif [ "$purge" -eq 0 ]; then
        die "MM-Companion is not installed in $APP_DIR"
    fi
    # --purge still runs with nothing installed: it is how data left behind by
    # an earlier plain uninstall is deleted.
    if [ "$purge" -eq 1 ]; then
        if [ "$mode" = system ]; then
            say "--purge only deletes the workspace of a per-user install; each user's"
            say "data in ~/.local/share/MM-Companion was left alone."
        elif [ -d "$WORKSPACE" ]; then
            rm -rf "$WORKSPACE"
            say "Deleted your workspace at $WORKSPACE."
        else
            say "There is no workspace at $WORKSPACE to delete."
        fi
    elif [ -d "$WORKSPACE" ]; then
        say "Your characters, mods and settings are still in $WORKSPACE"
        say "(delete that folder to remove them too)."
    fi
}

install_app() {
    here=$(cd "$(dirname "$0")" && pwd -P)
    [ -x "$here/$EXE" ] || die "no $EXE beside this script - run it from the extracted release folder"
    if [ -d "$APP_DIR" ] && [ "$(cd "$APP_DIR" && pwd -P)" = "$here" ]; then
        die "this is the installed copy; run the install.sh from a newer release folder to upgrade"
    fi
    version=$(cat "$here/VERSION" 2>/dev/null || echo unknown)
    previous=$(cat "$APP_DIR/VERSION" 2>/dev/null || true)

    # Copy beside the old install first and swap at the end, so a copy that
    # fails part-way leaves the working install in place.
    mkdir -p "$(dirname "$APP_DIR")" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
    rm -rf "$APP_DIR.new"
    cp -R "$here/." "$APP_DIR.new/"
    rm -rf "$APP_DIR"
    mv "$APP_DIR.new" "$APP_DIR"

    # A wrapper rather than a symlink: the frozen app finds its bundled files
    # relative to the path it was started by.
    cat >"$LAUNCHER" <<EOF
#!/bin/sh
exec "$APP_DIR/$EXE" "\$@"
EOF
    chmod 755 "$LAUNCHER"

    cp "$APP_DIR/$APP_ID.png" "$ICON_DIR/$APP_ID.png"
    cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=MM-Companion
GenericName=Character Sheet and Dice Roller
Comment=Dice roller and character creator for Mutants & Masterminds
Exec="$APP_DIR/$EXE"
Icon=$APP_ID
Terminal=false
Categories=Game;RolePlaying;
StartupWMClass=$EXE
EOF
    chmod 644 "$DESKTOP_FILE"
    if [ "$mode" = system ]; then
        chmod -R a+rX "$APP_DIR"
    fi
    refresh_caches

    if [ -z "$previous" ]; then
        say "Installed MM-Companion $version in $APP_DIR."
    elif [ "$previous" = "$version" ]; then
        say "Reinstalled MM-Companion $version in $APP_DIR."
    else
        say "Upgraded MM-Companion $previous -> $version in $APP_DIR."
    fi
    say "Start it from your applications menu, or run: $APP_ID"
    case :$PATH: in
        *:"$BIN_DIR":*) ;;
        *) say "(Note: $BIN_DIR is not on your PATH, so the '$APP_ID' command needs its full path.)" ;;
    esac
    say "To remove it later: $APP_DIR/install.sh --uninstall$([ "$mode" = system ] && printf ' --system')"
}

if [ "$action" = uninstall ]; then
    uninstall
else
    [ "$purge" -eq 0 ] || die "--purge only goes with --uninstall"
    install_app
fi
